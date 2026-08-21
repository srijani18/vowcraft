"""Persisting segments — shared between the upload pipeline and live capture (SPEC-013).

Extracted out of `pipeline.py` unchanged so both callers write `Speaker`/`Segment`/`Word`
rows through the exact same code, rather than a second implementation that could drift
from the first. Takes the same `segments: list[dict]` shape `transcription.py`'s providers
already produce — `{"startMs", "endMs", "text", "speakerLabel", "words": [...]}`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from vowcraft_db import Segment, Speaker, Word


async def persist_segments(
    session: AsyncSession, transcript_id: str, segments: list[dict[str, Any]]
) -> None:
    """Replaces a transcript's segments wholesale — delete-then-insert, matching how a
    re-transcription already treats this data: there is no partial-segment state worth
    representing."""
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
