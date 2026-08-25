"""The team roster — SPEC-005 §4.2.

Field-level rules (a required name, a valid address, a length) live in `TeamService`, not in
the Pydantic models here, for the reason `settings.py` sets out at length: a Pydantic
constraint or a validator that raises lands in FastAPI's `RequestValidationError` handler,
which always answers `400 invalid_request`. These need their own `422` code
(`invalid_email`, `duplicate_email`, …) plus the `field` the form should highlight, so they
are explicit checks in the service.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep
from app.services.team import TeamService

router = APIRouter()


class MemberBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    role: Optional[str] = Field(default=None, max_length=200)


class MemberPatch(BaseModel):
    """Every field optional, because a PATCH may touch only one.

    `role` needs the absent/null distinction that a bare `Optional[str]` cannot express —
    "leave the role alone" and "clear the role" are different requests — so the route reads
    `model_fields_set` rather than checking for `None`.
    """

    name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=320)
    role: Optional[str] = Field(default=None, max_length=200)


@router.get("")
async def list_members(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await TeamService(session).list_members(user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_member(
    body: MemberBody, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    member = await TeamService(session).add(
        user.id, name=body.name, email=body.email, role=body.role, request_id=request_id
    )
    await session.commit()
    return {"member": member}


@router.patch("/{member_id}")
async def update_member(
    member_id: str,
    body: MemberPatch,
    user: CurrentUser,
    session: SessionDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    member = await TeamService(session).update(
        user.id,
        member_id,
        name=body.name,
        email=body.email,
        role=body.role,
        role_provided="role" in body.model_fields_set,
        request_id=request_id,
    )
    await session.commit()
    return {"member": member}


@router.delete("/{member_id}")
async def remove_member(
    member_id: str, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    result = await TeamService(session).remove(user.id, member_id, request_id)
    await session.commit()
    return result
