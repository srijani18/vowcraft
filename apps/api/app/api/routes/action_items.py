"""Action items — SPEC-001.

Routes bind, authenticate, delegate and translate. Every computed field (readiness, risk,
gate, canExecute) comes from the service, which recomputes it per read — see the note in
``services/action_items.py`` for why none of it is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep, SettingsDep
from app.services.action_items import ActionItemService
from app.services.executor import ExecutorService

router = APIRouter()

#: Wire name (camelCase, matching the request body) -> ORM/service attribute name.
#: `ActionItemService.patch_item` takes only the keys the client actually sent, so an
#: omitted field and one explicitly set to null are both simply absent here — see the
#: docstring on `patch_item` for why that is safe for both real callers.
_PATCH_FIELD_MAP = {
    "description": "description",
    "ownerName": "owner_name",
    "ownerEmail": "owner_email",
    "deadline": "deadline",
    "priority": "priority",
    "actionType": "action_type",
    "payload": "payload",
}


class PatchBody(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    ownerName: Optional[str] = Field(default=None, max_length=200)
    ownerEmail: Optional[str] = None
    deadline: Optional[datetime] = None
    priority: Optional[str] = None
    actionType: Optional[str] = None
    confidence: Optional[str] = None
    payload: Optional[dict] = None
    decisionNote: Optional[str] = Field(default=None, max_length=1000)

    def correctable_fields(self) -> dict[str, Any]:
        sent = self.model_dump(exclude_unset=True)
        return {_PATCH_FIELD_MAP[k]: v for k, v in sent.items() if k in _PATCH_FIELD_MAP}


class ExecuteBody(BaseModel):
    """The execute request — SPEC-002 §5."""

    payloadOverride: Optional[dict] = None
    dryRun: bool = False
    # Lets a caller supply their own key, so a client that retries a dropped response can
    # reuse it and get the replay rather than a second dispatch.
    idempotencyKey: Optional[str] = Field(default=None, min_length=8, max_length=200)
    # Accepted for wire compatibility with the modal that collects it, same as in
    # executeSchema on the TypeScript side — neither implementation currently enforces
    # that every WARN-level violation shown to the reviewer is named here.
    acknowledgedWarnings: Optional[list[str]] = None
    # Stricter than the original TypeScript, deliberately: gateSatisfied() there checks
    # only `status`, so EXPLICIT_APPROVAL_WITH_CONFIRMATION never actually required
    # anything beyond ordinary approval despite its name. This field closes that gap —
    # see gate_satisfied() in app/domain/risk.py — and ExecuteModal.tsx sends it once the
    # reviewer has clicked through the confirmation step.
    confirmed: bool = False


class BulkBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    op: Literal["approve", "reject", "defer"]
    note: Optional[str] = Field(default=None, max_length=1000)


def _service(session, settings) -> ActionItemService:
    return ActionItemService(session, settings)


def _split(values: Optional[list[str]]) -> Optional[list[str]]:
    """`?status=A&status=B` and `?status=A,B` both work — matching `multi()` in
    src/server/action-items/service.ts, which is equally permissive."""
    if not values:
        return None
    flat = [part for value in values for part in value.split(",") if part]
    return flat or None


@router.get("")
async def list_items(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    status: Annotated[Optional[list[str]], Query()] = None,
    priority: Annotated[Optional[list[str]], Query()] = None,
    action_type: Annotated[Optional[list[str]], Query(alias="type")] = None,
    readiness: Annotated[Optional[list[str]], Query()] = None,
    owner: Annotated[Optional[str], Query(max_length=200)] = None,
    deadline: Annotated[Optional[str], Query()] = None,
    transcriptId: Annotated[Optional[str], Query()] = None,
    q: Annotated[Optional[str], Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[Optional[str], Query()] = None,
) -> dict:
    return await _service(session, settings).list_items(
        user.id, user.email,
        status=_split(status), priority=_split(priority), action_type=_split(action_type),
        readiness=_split(readiness), owner=owner, deadline=deadline,
        transcript_id=transcriptId, search=q, limit=limit, cursor=cursor,
    )


@router.get("/{item_id}")
async def get_item(item_id: str, user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await _service(session, settings).get_item(user.id, user.email, item_id)


@router.patch("/{item_id}")
async def patch_item(
    item_id: str,
    body: PatchBody,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request_id: RequestIdDep,
) -> dict:
    result = await _service(session, settings).patch_item(
        user.id, user.email, item_id,
        status=body.status, fields=body.correctable_fields(),
        confidence=body.confidence, decision_note=body.decisionNote,
        request_id=request_id,
    )
    # One commit at the boundary: the status change and its audit row land together, or
    # neither does. A decision with no audit trail is a compliance defect (SPEC-003 §7).
    await session.commit()
    return result


@router.post("/bulk")
async def bulk(
    body: BulkBody,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request_id: RequestIdDep,
) -> dict:
    result = await _service(session, settings).bulk_update(
        user.id, user.email, body.ids, body.op, request_id, note=body.note
    )
    await session.commit()
    return result


@router.post("/{item_id}/execute")
async def execute(
    item_id: str,
    body: ExecuteBody,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request_id: RequestIdDep,
) -> dict:
    """The one path by which anything reaches a third party — SPEC-002 §5.

    No commit here: the executor manages its own transaction boundaries, because the status
    claim, the attempt rows and the final result-plus-audit each have to commit at different
    moments for the pipeline to be crash-safe.
    """
    return await ExecutorService(session, settings).execute(
        user.id, user.email, item_id,
        payload_override=body.payloadOverride,
        idempotency_key=body.idempotencyKey,
        confirmed=body.confirmed,
        dry_run=body.dryRun,
        request_id=request_id,
    )
