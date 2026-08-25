"""Transcripts — SPEC-010 read model, SPEC-012 reader.

Read and delete only. The ingest pipeline (upload, FFmpeg demux, transcription, extraction)
is a separate concern and is ported separately; this module is what the library, the reader
and the export need.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from vowcraft_db import ActionItem, Decision, Segment, Speaker, Transcript, TranscriptAsset, Word

from app.core.exceptions import not_found, unprocessable
from app.db.repositories.users import AuditRepository


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


class TranscriptService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _counts_for(self, transcript_id: str) -> dict[str, int]:
        segments = await self.session.scalar(
            select(func.count()).select_from(Segment).where(Segment.transcript_id == transcript_id)
        )
        items = await self.session.scalar(
            select(func.count()).select_from(ActionItem).where(ActionItem.transcript_id == transcript_id)
        )
        decisions = await self.session.scalar(
            select(func.count()).select_from(Decision).where(Decision.transcript_id == transcript_id)
        )
        return {"segments": segments or 0, "actionItems": items or 0, "decisions": decisions or 0}

    async def _summary_row(self, row: Transcript, counts: dict[str, int]) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "sourceType": _enum(row.source_type),
            "status": _enum(row.status),
            "stage": row.stage,
            "progress": row.progress,
            "language": row.language,
            "durationMs": row.duration_ms,
            "diarized": row.diarized,
            "summary": row.summary,
            "recordedAt": _iso(row.recorded_at),
            "createdAt": _iso(row.created_at_),
            "transcribeProvider": row.transcribe_provider,
            "extractProvider": row.extract_provider,
            "transcribeError": row.transcribe_error,
            "extractError": row.extract_error,
            # The machine-readable reason alongside the sentence: "add a key" and "the
            # configured model does not exist" both fail extraction but have different
            # fixes (SPEC-010 §3.4).
            "extractErrorCode": row.extract_error_code,
            "extractModel": row.extract_model,
            "counts": counts,
        }

    async def list_transcripts(self, user_id: str) -> dict[str, Any]:
        rows = (
            await self.session.scalars(
                select(Transcript)
                .where(Transcript.user_id == user_id)
                .order_by(Transcript.created_at_.desc())
                .limit(100)
            )
        ).all()

        # Three grouped queries for the whole page rather than three per row: a 100-row
        # list was issuing up to 300 COUNT queries.
        ids = [r.id for r in rows]
        empty: dict[str, int] = {}
        segment_counts = empty if not ids else dict(
            (
                await self.session.execute(
                    select(Segment.transcript_id, func.count())
                    .where(Segment.transcript_id.in_(ids))
                    .group_by(Segment.transcript_id)
                )
            ).all()
        )
        item_counts = empty if not ids else dict(
            (
                await self.session.execute(
                    select(ActionItem.transcript_id, func.count())
                    .where(ActionItem.transcript_id.in_(ids))
                    .group_by(ActionItem.transcript_id)
                )
            ).all()
        )
        decision_counts = empty if not ids else dict(
            (
                await self.session.execute(
                    select(Decision.transcript_id, func.count())
                    .where(Decision.transcript_id.in_(ids))
                    .group_by(Decision.transcript_id)
                )
            ).all()
        )

        return {
            "transcripts": [
                await self._summary_row(
                    r,
                    {
                        "segments": segment_counts.get(r.id, 0),
                        "actionItems": item_counts.get(r.id, 0),
                        "decisions": decision_counts.get(r.id, 0),
                    },
                )
                for r in rows
            ]
        }

    async def get_transcript(self, user_id: str, transcript_id: str) -> dict[str, Any]:
        row = await self.session.scalar(
            select(Transcript).where(
                Transcript.id == transcript_id, Transcript.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That transcript does not exist.")

        segments = (
            await self.session.scalars(
                select(Segment)
                .where(Segment.transcript_id == transcript_id)
                .order_by(Segment.start_ms)
                .options(selectinload(Segment.words), selectinload(Segment.speaker))
            )
        ).all()
        speakers = (
            await self.session.scalars(
                select(Speaker)
                .where(Speaker.transcript_id == transcript_id)
                .order_by(Speaker.label)
            )
        ).all()

        summary = await self._summary_row(row, await self._counts_for(transcript_id))
        summary["segments"] = [
            {
                "id": s.id,
                "startMs": s.start_ms,
                "endMs": s.end_ms,
                "text": s.text,
                # The override if set, otherwise the machine's label — so a rename applies
                # everywhere without rewriting the diarizer's output.
                "speakerLabel": (
                    (s.speaker.display_name or s.speaker.label) if s.speaker else None
                ),
                "speakerId": s.speaker_id,
                "words": [
                    {"text": w.text, "startMs": w.start_ms, "endMs": w.end_ms}
                    for w in sorted(s.words, key=lambda w: w.start_ms)
                ],
            }
            for s in segments
        ]
        summary["speakers"] = [
            {"id": sp.id, "label": sp.label, "displayName": sp.display_name} for sp in speakers
        ]
        # Reported from the stored asset rather than inferred from `sourceType`, because the
        # two can disagree: a live capture (SPEC-013) keeps only segments — the tab's audio
        # is never persisted — but an upload whose asset was pruned is equally audio-less.
        # The reader needs to know whether a player can work, not how the words arrived.
        summary["hasAudio"] = (
            await self.session.scalar(
                select(func.count())
                .select_from(TranscriptAsset)
                .where(TranscriptAsset.transcript_id == transcript_id)
            )
        ) > 0
        return summary

    async def rename_speakers(
        self, user_id: str, transcript_id: str, renames: list[tuple[str, Optional[str]]]
    ) -> dict[str, Any]:
        """A port of the batch PATCH in src/app/api/transcripts/[id]/speakers/route.ts —
        up to 50 renames in one request, so relabelling everyone in a meeting is one
        save rather than one round trip per speaker.

        Two separate checks, in this order, matching the TS route exactly: does the
        transcript exist at all (404 if not), then does every named speaker actually
        belong to it (422 if not) — collapsing them into one query answers a missing
        transcript with the same "unknown speaker" code as a real ownership violation,
        which points a caller at the wrong problem.
        """
        transcript = await self.session.scalar(
            select(Transcript).where(Transcript.id == transcript_id, Transcript.user_id == user_id)
        )
        if transcript is None:
            raise not_found("That transcript does not exist.")

        owned = set(
            (
                await self.session.scalars(
                    select(Speaker.id).where(Speaker.transcript_id == transcript_id)
                )
            ).all()
        )
        unknown = [speaker_id for speaker_id, _ in renames if speaker_id not in owned]
        if unknown:
            raise unprocessable(
                "unknown_speaker", "One of those speakers does not belong to this transcript."
            )

        for speaker_id, display_name in renames:
            speaker = await self.session.get(Speaker, speaker_id)
            # Null clears the override and falls back to the original label — an
            # empty-after-trim string means the same thing, not a blank display name.
            speaker.display_name = display_name.strip() if display_name and display_name.strip() else None
        await self.session.flush()

        speakers = (
            await self.session.scalars(
                select(Speaker)
                .where(Speaker.transcript_id == transcript_id)
                .order_by(Speaker.label)
            )
        ).all()
        return {
            "ok": True,
            "speakers": [
                {"id": sp.id, "label": sp.label, "displayName": sp.display_name} for sp in speakers
            ],
        }

    async def delete_transcript(self, user_id: str, transcript_id: str, request_id: str) -> None:
        row = await self.session.scalar(
            select(Transcript).where(
                Transcript.id == transcript_id, Transcript.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That transcript does not exist.")
        title = row.title
        # Segments, words, speakers, action items, decisions and the stored audio all
        # cascade at the database level.
        await self.session.delete(row)
        await AuditRepository(self.session).record(
            event="transcript.deleted", actor_id=user_id, request_id=request_id,
            metadata={"transcriptId": transcript_id, "title": title},
        )

    async def audio(self, user_id: str, transcript_id: str) -> tuple[bytes, str, str]:
        asset = await self.session.scalar(
            select(TranscriptAsset)
            .join(Transcript, Transcript.id == TranscriptAsset.transcript_id)
            .where(
                TranscriptAsset.transcript_id == transcript_id, Transcript.user_id == user_id
            )
        )
        if asset is None:
            raise not_found("No audio is stored for that transcript.")
        return asset.data, asset.mime_type, asset.filename
