"""Upload and the background pipeline — SPEC-010 §5.

Two stages, run after the upload response has already gone out: transcribe, then extract.
Each fails independently — a transcript whose extraction fails keeps its transcription and
can be re-extracted alone (SPEC-010 §3.4), and a transcript whose transcription fails is
never left half-written, because the second stage only starts once the first has committed.

Persisting the transcript *before* attempting extraction is deliberate for the same reason:
a later failure in the second stage must not cost the first stage's work.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from vowcraft_db import Segment, Speaker, Transcript, TranscriptAsset, Word

from app.core.config import get_settings
from app.core.logging import logger
from app.db.session import session_factory
from app.services.credentials import CredentialService
from app.services.ingest.audio import extract_audio
from app.services.ingest.validate import MAX_AUDIO_BYTES
from app.services.transcription import TranscriptionError, resolve_transcriber, sample_transcribe, transcribe


def _container_of(filename: str, mime_type: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return (mime_type.split("/")[-1] or "bin").lower()


async def _mark(session: AsyncSession, transcript_id: str, **fields: Any) -> None:
    row = await session.get(Transcript, transcript_id)
    if row is None:
        return
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.commit()


def start_processing(transcript_id: str, user_id: str, request_id: str) -> None:
    """Fires the pipeline without awaiting it — the caller has already answered 202.

    A bare `asyncio.create_task` reference would be garbage-collected once this function
    returns and the task could be cancelled mid-flight; keeping a module-level set of live
    tasks is what stops that.
    """
    task = asyncio.create_task(_run_guarded(transcript_id, user_id, request_id))
    _LIVE_TASKS.add(task)
    task.add_done_callback(_LIVE_TASKS.discard)


_LIVE_TASKS: set[asyncio.Task] = set()


async def _run_guarded(transcript_id: str, user_id: str, request_id: str) -> None:
    try:
        await run_pipeline(transcript_id, user_id, request_id)
    except Exception as exc:  # noqa: BLE001 — this is the backstop; nothing above it catches
        logger.error("pipeline.unhandled", transcriptId=transcript_id, err=exc)
        async with session_factory()() as session:
            try:
                await _mark(
                    session, transcript_id, status="FAILED", stage="done", progress=100,
                    transcribe_error=f"Unexpected failure: {exc}",
                )
            except Exception:  # noqa: BLE001 — a failure to record the failure is not fatal
                pass


async def run_pipeline(transcript_id: str, user_id: str, request_id: str) -> None:
    settings = get_settings()
    log = logger.child(requestId=request_id, transcriptId=transcript_id, userId=user_id)

    async with session_factory()() as session:
        transcript = await session.scalar(
            select(Transcript).where(Transcript.id == transcript_id, Transcript.user_id == user_id)
        )
        asset = await session.scalar(
            select(TranscriptAsset).where(TranscriptAsset.transcript_id == transcript_id)
        )
        if transcript is None or asset is None:
            log.error("pipeline.no_asset")
            return

        # ── stage 1: transcribe
        await _mark(session, transcript_id, stage="transcribing", progress=15, status="PROCESSING")

        credentials = CredentialService(session, settings)
        try:
            # `allow_sample=True`: with nothing configured, the bundled fixture stands
            # in — the whole pipeline (upload, word timings, extraction, guardrails, the
            # board) has to be exercisable with no API key, or a fresh `docker compose up`
            # shows a form that immediately demands a credential instead of a demo.
            provider, api_key, source = await resolve_transcriber(user_id, credentials, allow_sample=True)
        except Exception as exc:  # noqa: BLE001 — service_unavailable, but caught generically
            log.warn("pipeline.no_transcriber")
            message = getattr(exc, "message", None) or (
                "No transcription provider is configured. Add a Groq key in "
                "Settings → API keys — it is free."
            )
            await _mark(
                session, transcript_id, stage="done", progress=100, status="FAILED",
                transcribe_error=message,
            )
            return
        log.info("pipeline.transcribing", provider=provider.id, source=source)

        container = _container_of(asset.filename, asset.mime_type)
        is_video = asset.mime_type.startswith("video/")
        try:
            demuxed = await extract_audio(
                data=bytes(asset.data), filename=asset.filename, mime_type=asset.mime_type,
                container=container, is_video=is_video,
                max_bytes=min(provider.max_bytes, MAX_AUDIO_BYTES) if provider.max_bytes else MAX_AUDIO_BYTES,
            )
            if demuxed.transcoded:
                await _mark(session, transcript_id, stage="transcribing", progress=35)
                log.info(
                    "pipeline.audio_extracted",
                    container=container, hadVideo=is_video,
                    originalBytes=demuxed.original_bytes, audioBytes=len(demuxed.data),
                    reduction=(
                        round(1 - len(demuxed.data) / demuxed.original_bytes, 3)
                        if demuxed.original_bytes else 0
                    ),
                    durationMs=demuxed.duration_ms,
                )

            # The sample provider ignores its input entirely (it always returns the same
            # fixture) but demuxing still runs unconditionally ahead of it, matching the
            # TS pipeline exactly — so the diagnostics above fire in sample mode too.
            result = (
                await sample_transcribe()
                if provider.id == "sample"
                else await transcribe(
                    provider, data=demuxed.data, filename=demuxed.filename,
                    mime_type=demuxed.mime_type, api_key=api_key,
                )
            )
            segments = result["segments"]
            language = result["language"]
            # FFmpeg's duration is authoritative when we demuxed: it read the container,
            # whereas a provider reports only what it transcribed.
            duration_ms = demuxed.duration_ms or result["durationMs"]
            diarized = result["diarized"]
            transcribe_provider = result["provider"]
        except (TranscriptionError, Exception) as exc:  # noqa: BLE001
            message = (
                exc.message if isinstance(exc, TranscriptionError)
                else f"Transcription failed: {exc}"
            )
            log.error("pipeline.transcribe_failed", err=exc)
            await _mark(
                session, transcript_id, stage="done", progress=100, status="FAILED",
                transcribe_error=message,
            )
            return

        # ── persist before attempting extraction — see the module docstring
        await session.execute(delete(Segment).where(Segment.transcript_id == transcript_id))
        await session.execute(delete(Speaker).where(Speaker.transcript_id == transcript_id))
        await session.flush()

        speaker_ids: dict[str, str] = {}
        labels = {s["speakerLabel"] for s in segments if s.get("speakerLabel")}
        for label in labels:
            speaker = Speaker(transcript_id=transcript_id, label=label)
            session.add(speaker)
            await session.flush()
            speaker_ids[label] = speaker.id

        for segment in segments:
            row = Segment(
                transcript_id=transcript_id,
                speaker_id=speaker_ids.get(segment.get("speakerLabel")),
                start_ms=segment["startMs"], end_ms=segment["endMs"], text=segment["text"],
            )
            session.add(row)
            await session.flush()
            for word in segment.get("words", []):
                session.add(
                    Word(
                        segment_id=row.id, text=word["text"], start_ms=word["startMs"],
                        end_ms=word["endMs"], confidence=word.get("confidence"),
                    )
                )

        transcript.language = language
        transcript.duration_ms = duration_ms
        transcript.diarized = diarized
        transcript.transcribe_provider = transcribe_provider
        transcript.transcribed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        transcript.transcribe_error = None
        transcript.stage = "extracting"
        transcript.progress = 65
        await session.commit()

        log.info(
            "pipeline.transcribed", provider=transcribe_provider,
            segments=len(segments), diarized=diarized,
        )

    # ── stage 2: extract, via the existing BRD/extraction LLM layer
    from app.services.extraction import extract_into

    outcome = await extract_into(transcript_id, user_id, request_id)

    # ── stage 3: embed, for semantic search (SPEC-021) — best-effort, same as extraction:
    # a transcript with no embedding provider configured still finishes as READY.
    from app.services.embeddings import embed_segments_into

    try:
        async with session_factory()() as session:
            embed_outcome = await embed_segments_into(
                session, transcript_id, user_id, CredentialService(session, settings)
            )
    except Exception as exc:  # noqa: BLE001 — never let embedding cost the pipeline's success
        log.error("pipeline.embed_unhandled", err=exc)
        embed_outcome = {"ok": False, "embedded": 0, "reason": "unexpected"}

    async with session_factory()() as session:
        # Status only; extract_into owns extractError/extractErrorCode/extractModel on
        # both its paths — writing them again here would leave a stale code behind a
        # fresh message when the two disagreed.
        await _mark(session, transcript_id, stage="done", progress=100, status="READY")

    log.info(
        "pipeline.done", extracted=outcome.get("created", 0), extractionOk=outcome.get("ok"),
        embedded=embed_outcome.get("embedded", 0), embeddingOk=embed_outcome.get("ok"),
    )
