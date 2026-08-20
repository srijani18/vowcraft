"""Audit log — SPEC-003 §7. Read-only; there is no write path here by design."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, SessionDep
from app.services.audit import AuditService

router = APIRouter()


@router.get("")
async def list_audit(
    user: CurrentUser,
    session: SessionDep,
    event: Annotated[Optional[list[str]], Query()] = None,
    actionItemId: Annotated[Optional[str], Query()] = None,
    q: Annotated[Optional[str], Query(max_length=200)] = None,
    since: Annotated[Optional[datetime], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[Optional[str], Query()] = None,
) -> dict:
    # A caller may repeat `?event=a&event=b` or comma-join `?event=a,b`; the frontend only
    # does the former, but the query-string shape historically accepted both.
    events = [e for part in (event or []) for e in part.split(",") if e] or None
    return await AuditService(session).list_entries(
        user.id, events=events, action_item_id=actionItemId, search=q, since=since,
        limit=limit, cursor=cursor,
    )
