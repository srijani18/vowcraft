"""Decisions — SPEC-020. Read-only; there is no write path here by design."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, SessionDep
from app.services.decisions import DecisionService

router = APIRouter()


@router.get("")
async def list_decisions(
    user: CurrentUser,
    session: SessionDep,
    transcriptId: Annotated[Optional[str], Query()] = None,
    decidedBy: Annotated[Optional[str], Query(max_length=200)] = None,
    q: Annotated[Optional[str], Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[Optional[str], Query()] = None,
) -> dict:
    return await DecisionService(session).list_decisions(
        user.id, transcript_id=transcriptId, decided_by=decidedBy, search=q,
        limit=limit, cursor=cursor,
    )
