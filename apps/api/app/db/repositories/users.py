"""User persistence. SQL lives here; policy does not.

The split earns its keep at the boundary: a service can be reasoned about without knowing
whether a lookup is one query or three, and a query can be optimised without re-reading
the rules that call it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from voice2brd_db import AuditLog, User, UserSettings, now_ms


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_id(self, user_id: str) -> Optional[User]:
        return await self.session.scalar(select(User).where(User.id == user_id))

    async def by_email(self, email: str) -> Optional[User]:
        """Case-insensitive, because an email address is not case-sensitive in practice.

        Comparing with ``lower()`` on both sides rather than storing a normalised copy:
        the column already holds mixed-case addresses written by the TypeScript
        implementation, and rewriting them would be a data migration for no gain.
        """
        return await self.session.scalar(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )

    async def email_exists(self, email: str) -> bool:
        found = await self.session.scalar(
            select(User.id).where(func.lower(User.email) == email.strip().lower())
        )
        return found is not None

    async def create(
        self, *, email: str, name: Optional[str], password_hash: Optional[str]
    ) -> User:
        now = now_ms()
        user = User(
            email=email.strip(),
            name=name.strip() if name else None,
            password_hash=password_hash,
            # Set at creation so the first token's `pwd` claim has something to match.
            # Without it, every token would carry 0 and a later password change could not
            # revoke anything issued before it.
            password_updated_at=now if password_hash else None,
        )
        self.session.add(user)
        await self.session.flush()
        # Defaults live in the domain, not the column, so a new account is immediately
        # usable by the rule engine (SPEC-003 §2).
        self.session.add(UserSettings(user_id=user.id))
        await self.session.flush()
        return user

    async def set_password(self, user: User, password_hash: str) -> None:
        """Rotating the hash also rotates ``passwordUpdatedAt``, which revokes every
        token issued before now — that is the entire mechanism behind "sign out
        everywhere", with no session table to keep."""
        user.password_hash = password_hash
        # Truncated to milliseconds: the column cannot hold more, and the `pwd`
        # claim is compared exactly (see voice2brd_db.clock).
        user.password_updated_at = now_ms()
        await self.session.flush()

    async def touch_onboarding(self, user: User, *, action: str) -> None:
        """`complete` and `skip` are equally final (SPEC-005 §6): a tour that reappears
        after being dismissed reads as a bug, so both write the same terminal state.
        `restart` clears it so the tour can run again."""
        if action == "restart":
            user.onboarding_completed_at = None
            user.onboarding_skipped = False
        else:
            user.onboarding_completed_at = now_ms()
            user.onboarding_skipped = action == "skip"
        await self.session.flush()


class AuditRepository:
    """Append-only. There is deliberately no update or delete method here, and none
    anywhere else in the codebase (SPEC-003 §7)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        event: str,
        actor_id: Optional[str] = None,
        actor_type: str = "USER",
        action_item_id: Optional[str] = None,
        before: Optional[dict] = None,
        after: Optional[dict] = None,
        metadata: Optional[dict] = None,
        request_id: Optional[str] = None,
    ) -> None:
        self.session.add(
            AuditLog(
                event=event,
                actor_type=actor_type,
                actor_id=actor_id,
                action_item_id=action_item_id,
                before=before,
                after=after,
                metadata_=metadata,
                request_id=request_id,
            )
        )
        await self.session.flush()
