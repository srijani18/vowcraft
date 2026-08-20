"""Token issuing and verification — SPEC-006 §3, adapted for a cross-origin frontend.

The Next.js implementation used a signed cookie, which worked because page and API shared
an origin. With the frontend on Vercel and this service on Render they do not, so the
frontend carries a **bearer token** instead.

One property is deliberately carried over rather than reinvented: the ``pwd`` claim holds
``passwordUpdatedAt`` in **milliseconds**, and a token whose ``pwd`` disagrees with the
user's current value is rejected. That is what makes "changing your password signs out
every other device" work with no server-side session table. Milliseconds, not seconds:
at second granularity a token issued in the same second as a password change stayed
valid, which was a real hole in the original.

Two token types:

* **access** — short (30 min default), sent on every request.
* **refresh** — long (14 days), sent only to ``/auth/refresh``. Separated so an access
  token leaking from a log or a browser extension expires quickly, while the user is not
  asked to sign in twice a day.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import jwt
from voice2brd_db import to_epoch_ms

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Any failure to verify. Deliberately undifferentiated at the boundary.

    Distinguishing "expired" from "bad signature" for the caller is fine; distinguishing
    them for the *client* tells an attacker which of their guesses was closer.
    """


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    token_type: TokenType
    #: ``passwordUpdatedAt`` in milliseconds, or 0 when the account has no password
    #: (federated-only sign-in). Compared on every request to revoke on rotation.
    password_epoch_ms: int
    issued_at: int
    expires_at: int


def password_epoch_ms(password_updated_at: Optional[datetime]) -> int:
    """The ``pwd`` claim value for a user.

    Milliseconds, matching the TypeScript implementation exactly: a token minted in the
    same *second* as a password rotation must not survive it.
    """
    # Delegated so there is one definition of "naive means UTC" and one of the
    # millisecond truncation the column requires (see voice2brd_db.clock).
    return to_epoch_ms(password_updated_at)


def issue_token(
    *,
    subject: str,
    token_type: TokenType,
    password_epoch: int,
    secret: str,
    algorithm: str,
    ttl_seconds: int,
) -> str:
    if not secret:
        raise TokenError("JWT_SECRET is not configured; tokens cannot be issued.")
    now = int(time.time())
    payload = {
        "sub": subject,
        "typ": token_type,
        "pwd": password_epoch,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(
    token: str, *, secret: str, algorithm: str, expected_type: Optional[TokenType] = None
) -> TokenClaims:
    if not secret:
        raise TokenError("JWT_SECRET is not configured; tokens cannot be verified.")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            # `sub` and `exp` are load-bearing; a token missing either is not a token.
            options={"require": ["sub", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("The session token is not valid.") from exc

    token_type = payload.get("typ")
    if token_type not in ("access", "refresh"):
        raise TokenError("The session token is not valid.")
    if expected_type is not None and token_type != expected_type:
        # An access token must not be usable to refresh, and a refresh token must not be
        # usable as an access token — otherwise the short access TTL buys nothing.
        raise TokenError("The session token is not valid.")

    pwd = payload.get("pwd")
    if not isinstance(pwd, int):
        raise TokenError("The session token is not valid.")

    return TokenClaims(
        subject=str(payload["sub"]),
        token_type=token_type,
        password_epoch_ms=pwd,
        issued_at=int(payload["iat"]),
        expires_at=int(payload["exp"]),
    )


def claims_match_user(claims: TokenClaims, password_updated_at: Optional[datetime]) -> bool:
    """Whether a verified token still corresponds to the account's current password.

    A signature check alone is not enough: the token was validly issued, and the question
    is whether it has since been revoked by a password change.
    """
    return claims.password_epoch_ms == password_epoch_ms(password_updated_at)
