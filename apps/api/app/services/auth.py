"""Authentication — the rules, none of the HTTP and none of the SQL.

Ported from ``src/server/auth/*`` and the auth route handlers. Behaviour that matters is
preserved deliberately:

* **Login does not distinguish "no such account" from "wrong password".** One sentence
  covers both, because two sentences make the endpoint an account enumerator — someone can
  discover who has an account without ever guessing a password.
* **A password check runs even when the account does not exist.** Returning early on an
  unknown address makes the response measurably faster for absent accounts, which is the
  same disclosure by a different channel.
* **Signup on an existing address behaves like a conflict, not a hint.** The address is
  already the thing the caller supplied, so a conflict tells them nothing they did not
  know — but the message avoids confirming *why* beyond that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from voice2brd_db import User

from app.core.config import Settings
from app.core.exceptions import AppError, conflict, unauthorized, unprocessable
from app.core.logging import Logger
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.security import issue_token, password_epoch_ms
from app.db.repositories.users import AuditRepository, UserRepository
from app.domain.password_rules import validate_email, validate_name, validate_password
from app.schemas.auth import AuthResponse, TokenPair, UserView

# A real scrypt verification against a hash that belongs to nobody. Used when the email is
# unknown, so the response time does not reveal whether the account exists. Generated once
# at import: the cost is one hash at boot rather than one per failed login.
_DECOY_HASH = hash_password("a decoy passphrase that authenticates nothing")

_CREDENTIALS_REJECTED = "That email and password combination is not recognised."


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def to_view(user: User) -> UserView:
    return UserView(
        id=user.id,
        email=user.email,
        name=user.name,
        image=user.image,
        # Whether a password exists, never the hash. A federated-only account has none,
        # and the profile screen needs to know so it offers "set" rather than "change".
        hasPassword=bool(user.password_hash),
        onboardingCompletedAt=_iso(user.onboarding_completed_at),
        onboardingSkipped=user.onboarding_skipped,
        createdAt=_iso(user.created_at_),
    )


class AuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        audit: AuditRepository,
        settings: Settings,
        log: Logger,
        request_id: str,
    ) -> None:
        self.users = users
        self.audit = audit
        self.settings = settings
        self.log = log
        self.request_id = request_id

    # ── tokens ────────────────────────────────────────────────────────────────

    def issue_pair(self, user: User) -> TokenPair:
        epoch = password_epoch_ms(user.password_updated_at)
        common = {
            "subject": user.id,
            "password_epoch": epoch,
            "secret": self.settings.JWT_SECRET,
            "algorithm": self.settings.JWT_ALGORITHM,
        }
        return TokenPair(
            accessToken=issue_token(
                token_type="access", ttl_seconds=self.settings.ACCESS_TOKEN_TTL_SECONDS, **common
            ),
            refreshToken=issue_token(
                token_type="refresh", ttl_seconds=self.settings.REFRESH_TOKEN_TTL_SECONDS, **common
            ),
            expiresIn=self.settings.ACCESS_TOKEN_TTL_SECONDS,
        )

    # ── signup ────────────────────────────────────────────────────────────────

    async def signup(self, *, email: str, password: str, name: Optional[str]) -> AuthResponse:
        for problem in (
            validate_email(email),
            validate_name(name) if name is not None else None,
            validate_password(password, email=email, name=name),
        ):
            if problem:
                raise unprocessable(
                    problem.code, problem.message, {"field": problem.field}
                )

        if await self.users.email_exists(email):
            raise conflict(
                "email_taken",
                "An account already exists with that email address. Sign in instead, or reset "
                "your password if you have forgotten it.",
                {"field": "email"},
            )

        user = await self.users.create(
            email=email, name=name, password_hash=hash_password(password)
        )
        await self.audit.record(
            event="auth.signed_up",
            actor_id=user.id,
            request_id=self.request_id,
            metadata={"method": "password"},
        )
        self.log.info("auth.signed_up", userId=user.id)
        return AuthResponse(user=to_view(user), tokens=self.issue_pair(user))

    # ── login ─────────────────────────────────────────────────────────────────

    async def login(self, *, email: str, password: str) -> AuthResponse:
        user = await self.users.by_email(email)

        # Verify against a decoy when the account is unknown, so an absent address costs
        # the same ~100ms as a wrong password. Skipping this would let a timing loop
        # enumerate accounts even though both paths return the same sentence.
        stored = user.password_hash if user and user.password_hash else _DECOY_HASH
        matched = verify_password(password, stored)

        if user is None or not user.password_hash or not matched:
            await self.audit.record(
                event="auth.sign_in_failed",
                actor_id=user.id if user else None,
                actor_type="SYSTEM" if user is None else "USER",
                request_id=self.request_id,
                # The address is recorded because a burst against one account is the
                # signal worth having; the password never is.
                metadata={"email": email.strip().lower(), "reason": "credentials"},
            )
            self.log.warn("auth.sign_in_failed", emailKnown=user is not None)
            raise unauthorized(_CREDENTIALS_REJECTED)

        # A pending deletion deliberately does not block sign-in. The grace period exists
        # so the account can be recovered, and that recovery lives behind authentication —
        # refusing here would lock the user out of the only screen that can cancel it. The
        # UI surfaces the pending state and its effective date instead.

        # Opportunistic upgrade: the version prefix exists so the cost factor can be
        # raised, and a successful login is the only moment the plaintext is available to
        # re-hash with.
        if needs_rehash(user.password_hash):
            await self.users.set_password(user, hash_password(password))
            self.log.info("auth.password_rehashed", userId=user.id)

        await self.audit.record(
            event="auth.signed_in", actor_id=user.id, request_id=self.request_id,
            metadata={"method": "password"},
        )
        self.log.info("auth.signed_in", userId=user.id)
        return AuthResponse(user=to_view(user), tokens=self.issue_pair(user))

    # ── password change ───────────────────────────────────────────────────────

    async def change_password(self, user: User, *, current: Optional[str], new: str) -> TokenPair:
        # A live session is not enough to rotate a password — that check is what stops a
        # borrowed laptop becoming a stolen account. An account with no password yet
        # (Google-only sign-in) is setting one for the first time and has nothing to prove.
        first_time = user.password_hash is None
        if user.password_hash:
            if not current:
                raise AppError(403, "current_password_required", "Enter your current password to change it.")
            if not verify_password(current, user.password_hash):
                raise AppError(403, "current_password_incorrect", "That is not your current password.")

        problem = validate_password(new, email=user.email, name=user.name)
        if problem:
            raise unprocessable(problem.code, problem.message, {"field": problem.field})
        if verify_password(new, user.password_hash):
            raise unprocessable(
                "password_unchanged", "That is the password you already have. Choose a different one."
            )

        await self.users.set_password(user, hash_password(new))
        await self.audit.record(
            event="profile.password_changed", actor_id=user.id, request_id=self.request_id,
            # Whether one existed before, never any part of either value.
            metadata={"firstTime": first_time},
        )
        self.log.info("auth.password_changed", userId=user.id)
        # Rotating the hash revoked every existing token, including the one this request
        # arrived with. Returning a fresh pair means the caller stays signed in *here*
        # while every other device is signed out — which is the intent.
        return self.issue_pair(user)
