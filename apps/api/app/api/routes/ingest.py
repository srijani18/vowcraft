"""Upload and re-extraction — SPEC-010 §9.

Upload returns 202 (or 200 for an identical re-upload) without waiting for the pipeline —
transcription runs after the response, polled via `GET /api/transcripts/:id`.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from sqlalchemy import select
from vowcraft_db import Transcript, TranscriptAsset, now_ms

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep, SettingsDep
from app.core.exceptions import not_found
from app.core.logging import logger
from app.db.repositories.users import AuditRepository
from app.services.extraction import extract_into
from app.services.ingest.pipeline import start_processing
from app.services.ingest.validate import title_from_filename, validate_upload

router = APIRouter()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    request_id: RequestIdDep,
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None, max_length=200),
    recorded_at: Optional[str] = Form(default=None, alias="recordedAt"),
) -> dict:
    data = await file.read()
    validated = validate_upload(file.filename or "recording", data)

    existing = await session.scalar(
        select(TranscriptAsset)
        .join(Transcript, Transcript.id == TranscriptAsset.transcript_id)
        .where(TranscriptAsset.checksum == validated.checksum, Transcript.user_id == user.id)
    )
    reused_transcript = await session.get(Transcript, existing.transcript_id) if existing else None
    if reused_transcript is not None:
        transcript = reused_transcript
        logger.info("ingest.reused", requestId=request_id, transcriptId=transcript.id)
        # 200, not the route's default 202: nothing was queued, so "accepted for
        # processing" would be a lie — the identical bytes are already fully processed
        # (or in flight from the original upload) and this response changed nothing.
        response.status_code = status.HTTP_200_OK
        return {
            "ok": True,
            "transcript": {
                "id": transcript.id, "title": transcript.title,
                "status": transcript.status.value if hasattr(transcript.status, "value") else str(transcript.status),
                "stage": transcript.stage, "reused": True,
            },
            "message": "You have already uploaded this recording — opening the existing transcript.",
        }

    resolved_title = (title or "").strip() or title_from_filename(validated.filename)
    from app.domain.timeutil import read_date

    resolved_recorded_at = read_date(recorded_at) or now_ms()

    transcript = Transcript(
        user_id=user.id, title=resolved_title, source_type="UPLOAD",
        recorded_at=resolved_recorded_at, status="QUEUED", stage="uploaded", progress=5,
    )
    session.add(transcript)
    await session.flush()

    # The bytes and their transcript commit together, so there is never an asset without
    # a row or a row without its audio.
    session.add(
        TranscriptAsset(
            transcript_id=transcript.id, filename=validated.filename,
            mime_type=validated.detected.mime_type, byte_size=len(validated.data),
            checksum=validated.checksum, data=validated.data,
        )
    )
    await AuditRepository(session).record(
        event="transcript.uploaded", actor_id=user.id, request_id=request_id,
        metadata={
            "transcriptId": transcript.id, "filename": validated.filename,
            "byteSize": len(validated.data), "container": validated.detected.container,
        },
    )
    await session.commit()

    # Deliberately not awaited: the caller gets a 202 and polls (SPEC-010 §5).
    start_processing(transcript.id, user.id, request_id)

    return {
        "ok": True,
        "transcript": {
            "id": transcript.id, "title": transcript.title, "status": "QUEUED",
            "stage": "uploaded", "reused": False,
        },
        "message": "Uploaded. Transcription has started; this page will update as it progresses.",
    }


@router.post("/{transcript_id}/extract")
async def reextract(
    transcript_id: str, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict:
    """Re-runs extraction alone — SPEC-010 §8.

    Useful after adding a provider key, or when a model call rate-limited. Action items a
    human has already decided on are preserved; only untouched proposals are replaced.
    """
    row = await session.scalar(
        select(Transcript).where(Transcript.id == transcript_id, Transcript.user_id == user.id)
    )
    if row is None:
        raise not_found("That transcript does not exist.")
    row.stage = "extracting"
    row.progress = 65
    await session.commit()

    # `extract_into` opens its own session — it also runs from the background pipeline,
    # where no request-scoped session exists to reuse, so it cannot depend on one here
    # either. This route's `row` is therefore stale the moment that call returns; re-fetch
    # rather than write through a possibly-outdated instance.
    outcome = await extract_into(transcript_id, user.id, request_id)

    row = await session.scalar(select(Transcript).where(Transcript.id == transcript_id))
    if row is not None:
        row.stage = "done"
        row.progress = 100
        await session.commit()
    return outcome


@router.get("/pipeline-status")
async def pipeline_status(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    """Which stages are usable, and via which provider — drives the upload UI.

    A port of src/server/ingest/service.ts's pipeline-status endpoint (there at
    ``GET /api/pipeline/status``; kept under this router's own prefix here rather than
    duplicated as a second top-level route, since the frontend is repointed to whichever
    path FastAPI actually serves, not obligated to match the old one byte-for-byte).
    """
    from app.services.credentials import CredentialService
    from app.services.llm import LlmService
    from app.services.transcription import transcription_status

    credentials = CredentialService(session, settings)
    transcribe_status = await transcription_status(user.id, credentials)
    extract_status = await LlmService(credentials, settings).status(user.id)

    return {
        "transcribe": transcribe_status,
        "extract": extract_status,
        # Both transcription.py::sample_transcribe and llm.py::sample_extract exist
        # unconditionally, so the demo path always works regardless of configuration.
        "sampleAvailable": True,
        "ready": extract_status["available"],
    }
