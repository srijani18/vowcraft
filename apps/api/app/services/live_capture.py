"""Finalizing a live tab-audio capture into an ordinary transcript — SPEC-013.

The capture itself streams over the same relay live dictation already uses (SPEC-014 §3,
`app/services/speech.py` / `app/api/routes/speech.py`) — unmodified, since audio bytes are
audio bytes regardless of whether they came from a microphone or a shared browser tab.
This module is the one genuinely new piece: turning the browser's accumulated utterances
into `Segment` rows through the exact same `persist_segments()` the upload pipeline uses,
then handing off to the exact same extraction/embedding stages, unmodified.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from vowcraft_db import Transcript, now_ms

from app.core.exceptions import unprocessable
from app.db.repositories.users import AuditRepository
from app.services.ingest.pipeline import start_extraction_and_embedding
from app.services.ingest.segments import persist_segments


def _segments_from_utterances(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Utterance-level, not word-level: the live relay's frames (`Utterance(text,
    is_final)`) carry no timing at all, unlike an upload's provider response — so segment
    boundaries are synthesized from each utterance's client-reported arrival time
    (`atMs`), sorted and clamped to a strictly increasing sequence so an out-of-order or
    duplicate timestamp from the browser can never produce a zero-or-negative-length
    segment. Still no words: the relay's frames carry no per-word timing to build them from.

    Speaker labels *are* set now, when the provider supplied one. Deepgram diarizes on its
    streaming endpoint and the relay forwards the index it reports (see `speech.py`), so
    this is attribution the provider actually made rather than a "Speaker 1" invented here
    — which is what the previous version rightly refused to do when nothing upstream
    diarized at all. A provider that does not diarize still yields None throughout, and the
    reader goes on saying speakers were not separated.
    """
    ordered = sorted(utterances, key=lambda u: u["atMs"])
    segments: list[dict[str, Any]] = []
    cursor = 0
    for u in ordered:
        text = u["text"].strip()
        if not text:
            continue
        start = cursor
        end = max(u["atMs"], start + 1)
        # Provider indices start at 0; every label in this system reads from 1, matching
        # the upload path's Deepgram mapping and the bundled sample.
        speaker = u.get("speaker")
        segments.append(
            {
                "startMs": start,
                "endMs": end,
                "text": text,
                "speakerLabel": f"Speaker {int(speaker) + 1}" if speaker is not None else None,
                "words": [],
            }
        )
        cursor = end
    return segments


async def finalize_live_capture(
    session: AsyncSession,
    user_id: str,
    request_id: str,
    *,
    title: Optional[str],
    utterances: list[dict[str, Any]],
    duration_ms: int,
    provider: Optional[str],
) -> Transcript:
    segments = _segments_from_utterances(utterances)
    if not segments:
        raise unprocessable(
            "empty_transcript",
            "Nothing was captured. Share a tab that has audio and speak, then stop.",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    transcript = Transcript(
        user_id=user_id,
        title=(title or "").strip() or f"Live capture — {datetime.now(timezone.utc).strftime('%d %b, %H:%M')}",
        source_type="TAB_CAPTURE",
        recorded_at=now_ms(),
        status="PROCESSING",
        stage="extracting",
        progress=65,
        # Computed from what actually arrived, matching the upload path's rule: two or
        # more distinct labels is diarization, one is a distinction without a difference,
        # and none means the provider never attributed anything. Hardcoding False here
        # would have shown "speakers were not separated" over a transcript that plainly
        # separates them.
        diarized=len({s["speakerLabel"] for s in segments if s["speakerLabel"]}) > 1,
        transcribe_provider=provider or "browser_tab_capture",
        transcribed_at=now,
        duration_ms=max(duration_ms, segments[-1]["endMs"]),
    )
    session.add(transcript)
    await session.flush()

    await persist_segments(session, transcript.id, segments)

    await AuditRepository(session).record(
        event="transcript.live_captured", actor_id=user_id, request_id=request_id,
        metadata={
            "transcriptId": transcript.id, "utteranceCount": len(utterances),
            "durationMs": duration_ms,
        },
    )
    await session.commit()

    # Deliberately not awaited: the caller gets a 202 and polls, same as an upload.
    start_extraction_and_embedding(transcript.id, user_id, request_id)

    return transcript
