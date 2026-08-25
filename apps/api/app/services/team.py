"""The team roster — SPEC-005 §4.2.

The roster is a name-to-address book, not a permissions list. Nothing here grants anyone
access: a `TeamMember` row cannot sign in, and `email` is deliberately a plain column with
no foreign key to `User`, because the people you talk about in meetings mostly do not have
accounts.

What it *does* is the thing that makes an action executable. `_resolve_email` in
`domain/extraction.py` turns "send it to Priya" into `priya@acme.test`, and without that a
calendar invite has no attendee and an email has no recipient. The roster also feeds known
spellings into the extraction prompt, so the model stops inventing "Prea" and "Prya" as
separate people.

Until this module existed there was no way to add anyone: rows were created only at signup
(one, for yourself) and by the seed script. So the roster's whole purpose was unreachable
for every real name, and the guardrail that warned about it (`VAL_OWNER_KNOWN`) pointed at a
remedy nobody could perform — which is why that rule was removed rather than left nagging.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from vowcraft_db import TeamMember

from app.core.exceptions import not_found, unprocessable
from app.db.repositories.users import AuditRepository
# Imported rather than re-implemented: the guardrail engine validates recipients with this
# exact function (`VAL_EMAIL_FORMAT`), so reusing it guarantees an address that saves here
# cannot later block at execution time. A second regex would drift the moment either moved.
from app.domain.payload import is_valid_email

_MAX_MEMBERS = 500
_MAX_NAME = 120
_MAX_ROLE = 80


def _dto(row: TeamMember) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "email": row.email, "role": row.role}


class TeamService:
    def __init__(self, session) -> None:
        self.session = session
        self.audit = AuditRepository(session)

    async def list_members(self, user_id: str) -> dict[str, Any]:
        rows = (
            await self.session.scalars(
                select(TeamMember)
                .where(TeamMember.user_id == user_id)
                # By name, because this is read as an address book rather than a log.
                .order_by(func.lower(TeamMember.name))
            )
        ).all()
        return {"members": [_dto(row) for row in rows]}

    def _clean(
        self, *, name: Optional[str], email: Optional[str], role: Optional[str]
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        cleaned_name = name.strip() if isinstance(name, str) else None
        cleaned_email = email.strip().lower() if isinstance(email, str) else None
        # An empty role means "no role", not an empty string, so the DTO stays null-clean.
        cleaned_role = role.strip() if isinstance(role, str) else None
        cleaned_role = cleaned_role or None

        if cleaned_name is not None and not cleaned_name:
            raise unprocessable("invalid_name", "A name is required.", {"field": "name"})
        if cleaned_name and len(cleaned_name) > _MAX_NAME:
            raise unprocessable(
                "invalid_name", f"A name cannot be longer than {_MAX_NAME} characters.",
                {"field": "name"},
            )
        if cleaned_email is not None and not is_valid_email(cleaned_email):
            raise unprocessable(
                "invalid_email",
                "That does not look like an email address.",
                {"field": "email"},
            )
        if cleaned_role and len(cleaned_role) > _MAX_ROLE:
            raise unprocessable(
                "invalid_role", f"A role cannot be longer than {_MAX_ROLE} characters.",
                {"field": "role"},
            )
        return cleaned_name, cleaned_email, cleaned_role

    async def _find_by_email(self, user_id: str, email: str) -> Optional[TeamMember]:
        return await self.session.scalar(
            select(TeamMember).where(
                TeamMember.user_id == user_id, func.lower(TeamMember.email) == email
            )
        )

    async def add(
        self, user_id: str, *, name: str, email: str, role: Optional[str], request_id: str
    ) -> dict[str, Any]:
        clean_name, clean_email, clean_role = self._clean(name=name, email=email, role=role)
        assert clean_name and clean_email  # both required on this path

        # Checked explicitly rather than relying on the unique index: a caught
        # IntegrityError would have already poisoned the transaction, and the useful answer
        # here is "you already have them" rather than a 500.
        if await self._find_by_email(user_id, clean_email):
            raise unprocessable(
                "duplicate_email",
                f"{clean_email} is already on your roster.",
                {"field": "email"},
            )

        count = await self.session.scalar(
            select(func.count()).select_from(TeamMember).where(TeamMember.user_id == user_id)
        )
        if (count or 0) >= _MAX_MEMBERS:
            raise unprocessable(
                "roster_full", f"A roster holds at most {_MAX_MEMBERS} people."
            )

        row = TeamMember(user_id=user_id, name=clean_name, email=clean_email, role=clean_role)
        self.session.add(row)
        await self.session.flush()
        await self.audit.record(
            event="team_member.added", actor_id=user_id, request_id=request_id,
            # The address is the point of the row and appears in every execution payload
            # anyway, so recording it here leaks nothing new.
            after={"name": clean_name, "email": clean_email, "role": clean_role},
            metadata={"teamMemberId": row.id},
        )
        return _dto(row)

    async def update(
        self,
        user_id: str,
        member_id: str,
        *,
        name: Optional[str] = None,
        email: Optional[str] = None,
        role: Optional[str] = None,
        role_provided: bool = False,
        request_id: str = "",
    ) -> dict[str, Any]:
        row = await self.session.scalar(
            select(TeamMember).where(
                TeamMember.id == member_id, TeamMember.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That person is not on your roster.")

        clean_name, clean_email, clean_role = self._clean(name=name, email=email, role=role)
        before = _dto(row)

        if clean_email and clean_email != row.email:
            existing = await self._find_by_email(user_id, clean_email)
            if existing is not None and existing.id != row.id:
                raise unprocessable(
                    "duplicate_email",
                    f"{clean_email} is already on your roster.",
                    {"field": "email"},
                )
            row.email = clean_email
        if clean_name:
            row.name = clean_name
        # Distinguishes "clear the role" (explicit null) from "leave it alone" (absent),
        # which a plain falsy check would collapse into the same thing.
        if role_provided:
            row.role = clean_role

        await self.session.flush()
        await self.audit.record(
            event="team_member.updated", actor_id=user_id, request_id=request_id,
            before=before, after=_dto(row), metadata={"teamMemberId": row.id},
        )
        return _dto(row)

    async def remove(self, user_id: str, member_id: str, request_id: str) -> dict[str, Any]:
        row = await self.session.scalar(
            select(TeamMember).where(
                TeamMember.id == member_id, TeamMember.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That person is not on your roster.")
        before = _dto(row)
        await self.session.delete(row)
        await self.audit.record(
            event="team_member.removed", actor_id=user_id, request_id=request_id,
            before=before, metadata={"teamMemberId": member_id},
        )
        return {"ok": True}
