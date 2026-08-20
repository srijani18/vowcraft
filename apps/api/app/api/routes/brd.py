"""Requirements documents — SPEC-014 §8."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, OptionalUser, RequestIdDep, SessionDep, SettingsDep
from app.core.exceptions import AppError
from app.domain.brd import brd_filename, brd_to_markdown
from app.services.brd import BrdService

_FORMATS = {"md": "text/markdown; charset=utf-8", "json": "application/json; charset=utf-8"}

router = APIRouter()


class SpokenBody(BaseModel):
    # Matches src/server/brd/service.ts's spokenTextSchema exactly: a submission over this
    # is rejected outright, never silently truncated — see the note on `_check_spoken`.
    spokenText: str = Field(max_length=50_000)


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)


@router.get("")
async def list_documents(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    return await BrdService(session, settings).list_documents(user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: SpokenBody, user: CurrentUser, session: SessionDep, settings: SettingsDep,
    request_id: RequestIdDep,
) -> dict:
    service = BrdService(session, settings)
    try:
        result = await service.create(user.id, body.spokenText, request_id)
    except Exception:
        # Committed even on failure: the row holds the transcript and the failure reason,
        # which is the whole point of creating it before the model call.
        await session.commit()
        raise
    await session.commit()
    return result


@router.get("/{document_id}")
async def get_document(
    document_id: str, user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> dict:
    return await BrdService(session, settings).get_document(user.id, document_id)


@router.post("/{document_id}/refine")
async def refine(
    document_id: str, body: SpokenBody, user: CurrentUser, session: SessionDep,
    settings: SettingsDep, request_id: RequestIdDep,
) -> dict:
    service = BrdService(session, settings)
    try:
        result = await service.refine(user.id, document_id, body.spokenText, request_id)
    except Exception:
        await session.commit()
        raise
    await session.commit()
    return result


@router.patch("/{document_id}")
async def rename(
    document_id: str, body: RenameBody, user: CurrentUser, session: SessionDep,
    settings: SettingsDep, request_id: RequestIdDep,
) -> dict:
    result = await BrdService(session, settings).rename(user.id, document_id, body.title, request_id)
    await session.commit()
    return result


@router.delete("/{document_id}")
async def delete(
    document_id: str, user: CurrentUser, session: SessionDep, settings: SettingsDep,
    request_id: RequestIdDep,
) -> dict:
    result = await BrdService(session, settings).delete(user.id, document_id, request_id)
    await session.commit()
    return result


@router.get("/{document_id}/export")
async def export(
    document_id: str, request: Request, user: OptionalUser, session: SessionDep, settings: SettingsDep,
) -> Response:
    """Returns a file, not JSON: the caller is a link, so the browser saves it with no
    client code. PDF and DOCX are deliberately absent — export as Markdown and convert.

    Every error here is deliberately plain text, matching
    ``src/app/api/brd/[id]/export/route.ts`` exactly: a browser navigating a download link
    reads a status code, not a `.error.code` field, and `format`'s validation in particular
    must not be a Pydantic constraint — a `Query(pattern=...)` violation lands in FastAPI's
    generic `RequestValidationError` handler, which answers the JSON envelope instead of
    this route's own plain-text body (the same bug class documented in SPEC-015 §7 for the
    settings surface).
    """
    import json as _json

    if user is None:
        return Response("Unauthorized", status_code=401, media_type="text/plain")

    requested = (request.query_params.get("format") or "md").lower()
    if requested not in _FORMATS:
        return Response(
            f'Unsupported format "{requested}". Available: {", ".join(_FORMATS)}. '
            "PDF and DOCX are not included — export as Markdown and convert.",
            status_code=400, media_type="text/plain",
        )

    try:
        document = await BrdService(session, settings).get_document(user.id, document_id)
    except AppError:
        return Response("Not found", status_code=404, media_type="text/plain")

    if not document["content"]:
        return Response(
            "This document has no generated content yet, so there is nothing to export.",
            status_code=409, media_type="text/plain",
        )

    if requested == "json":
        body = _json.dumps(document["content"], indent=2)
    else:
        body = brd_to_markdown(
            document["content"],
            {"revisions": document["revisionCount"], "generatedAt": (document["updatedAt"] or "")[:10]},
        )

    return Response(
        content=body,
        media_type=_FORMATS[requested],
        headers={
            "content-disposition": f'attachment; filename="{brd_filename(document["title"], requested)}"',
            # The user's own requirements; no shared cache should hold them.
            "cache-control": "private, no-store",
        },
    )
