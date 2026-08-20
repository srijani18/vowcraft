"""Transcripts — SPEC-010 §9, SPEC-012 §6."""

from __future__ import annotations

import re
from typing import Annotated, Optional

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep
from app.services.transcripts import TranscriptService

router = APIRouter()

_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")


def resolve_range(range_header: Optional[str], total_length: int) -> Optional[tuple[Optional[int], int]]:
    """Parses a single-range `Range` header — a port of the regex-and-clamp logic in
    src/app/api/transcripts/[id]/audio/route.ts, which is what an `<audio>` element sends
    to seek.

    Returns ``None`` when there is no range to honour (absent, or a shape this endpoint
    does not support — multi-range, `bytes=-500` suffix form) — the caller serves the
    full body. Returns ``(None, total_length)`` as the sentinel for "the requested range
    cannot be satisfied" — the caller answers 416. Otherwise returns the inclusive
    ``(start, end)`` byte bounds to serve as 206.
    """
    if not range_header:
        return None
    match = _RANGE_PATTERN.fullmatch(range_header)
    if not match:
        return None
    start_str, end_str = match.groups()
    start = int(start_str) if start_str else 0
    end = min(int(end_str), total_length - 1) if end_str else total_length - 1
    if start > end or start >= total_length:
        return None, total_length
    return start, end


class SpeakerRename(BaseModel):
    id: str = Field(min_length=1)
    # Null clears the override and falls back to the original label.
    displayName: Optional[str] = Field(default=None, max_length=120)


class SpeakersBody(BaseModel):
    speakers: list[SpeakerRename] = Field(min_length=1, max_length=50)


@router.get("")
async def list_transcripts(user: CurrentUser, session: SessionDep) -> dict:
    return await TranscriptService(session).list_transcripts(user.id)


@router.get("/{transcript_id}")
async def get_transcript(transcript_id: str, user: CurrentUser, session: SessionDep) -> dict:
    return await TranscriptService(session).get_transcript(user.id, transcript_id)


@router.delete("/{transcript_id}")
async def delete_transcript(
    transcript_id: str, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict:
    await TranscriptService(session).delete_transcript(user.id, transcript_id, request_id)
    await session.commit()
    return {"ok": True}


@router.patch("/{transcript_id}/speakers")
async def rename_speakers(
    transcript_id: str, body: SpeakersBody, user: CurrentUser, session: SessionDep
) -> dict:
    result = await TranscriptService(session).rename_speakers(
        user.id, transcript_id, [(s.id, s.displayName) for s in body.speakers]
    )
    await session.commit()
    return result


@router.get("/{transcript_id}/audio")
async def audio(transcript_id: str, user: CurrentUser, session: SessionDep, request: Request) -> Response:
    """A port of src/app/api/transcripts/[id]/audio/route.ts, including a single-range
    `Range` request — what an `<audio>` element sends to seek. Without it, clicking a
    word three minutes in re-downloads the whole file to get there."""
    data, mime_type, filename = await TranscriptService(session).audio(user.id, transcript_id)
    safe_filename = filename.replace('"', "")

    headers = {
        "accept-ranges": "bytes",
        # Private: this is the user's own recording, so no shared cache should hold it.
        "cache-control": "private, max-age=3600",
        "content-disposition": f'inline; filename="{safe_filename}"',
    }

    parsed = resolve_range(request.headers.get("range"), len(data))
    if parsed is not None:
        start, end = parsed
        if start is None:
            return Response(
                content=b"", status_code=416,
                headers={"content-range": f"bytes */{len(data)}"},
            )
        chunk = data[start : end + 1]
        return Response(
            content=chunk, media_type=mime_type, status_code=206,
            headers={
                **headers,
                "content-range": f"bytes {start}-{end}/{len(data)}",
                "content-length": str(len(chunk)),
            },
        )

    return Response(
        content=data, media_type=mime_type,
        headers={**headers, "content-length": str(len(data))},
    )


@router.get("/{transcript_id}/export")
async def export(
    transcript_id: str,
    user: CurrentUser,
    session: SessionDep,
    format: Annotated[str, Query()] = "txt",
) -> Response:
    """A port of src/app/api/transcripts/[id]/export/route.ts.

    Two of its response bodies are deliberately plain text rather than the usual JSON
    error envelope, matching the original exactly: the caller here is a browser
    navigating a download link, not a fetch reading a `.error.code`.
    """
    from app.services.transcript_export import EXPORT_FORMATS, export_filename, render_export

    requested = format.lower()
    if requested not in EXPORT_FORMATS:
        message = (
            f'Unsupported format "{requested}". Available: {", ".join(EXPORT_FORMATS)}. '
            "PDF and DOCX are not included — export as text or Markdown and convert."
        )
        return Response(content=message, status_code=400, media_type="text/plain")

    transcript = await TranscriptService(session).get_transcript(user.id, transcript_id)
    if not transcript["segments"]:
        return Response(
            content="That transcript has no text yet.", status_code=409, media_type="text/plain"
        )

    body = render_export(transcript, requested)
    return Response(
        content=body,
        media_type=EXPORT_FORMATS[requested],
        headers={
            "content-disposition": f'attachment; filename="{export_filename(transcript["title"], requested)}"',
            "cache-control": "private, no-store",
        },
    )
