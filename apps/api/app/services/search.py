"""Semantic search over transcript segments — SPEC-021.

Ranking is cosine distance via pgvector's `<=>` operator (SQLAlchemy-pgvector's
``cosine_distance()`` comparator compiles to it), matching both the `SegmentEmbedding`
table's HNSW index (`vector_cosine_ops`) and Voyage's own recommended metric for retrieval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from vowcraft_db import Segment, SegmentEmbedding, Speaker, Transcript

from app.domain.action_item import timestamp_label

_MAX_LIMIT = 50


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _speaker_label(speaker: Optional[Speaker]) -> Optional[str]:
    return (speaker.display_name or speaker.label) if speaker else None


class SearchService:
    def __init__(self, session) -> None:
        self.session = session

    async def search(
        self,
        user_id: str,
        query_embedding: list[float],
        *,
        transcript_id: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        distance = SegmentEmbedding.embedding.cosine_distance(query_embedding)
        stmt = (
            select(Segment, Transcript, distance.label("distance"))
            .join(SegmentEmbedding, SegmentEmbedding.segment_id == Segment.id)
            .join(Transcript, Transcript.id == Segment.transcript_id)
            .options(selectinload(Segment.speaker))
            .where(Transcript.user_id == user_id)
        )
        if transcript_id:
            stmt = stmt.where(Transcript.id == transcript_id)
        stmt = stmt.order_by(distance).limit(min(limit, _MAX_LIMIT))
        rows = (await self.session.execute(stmt)).all()

        # Neighbouring-segment context so a hit is a readable moment, not a bare fragment
        # — one small indexed query per hit (at most _MAX_LIMIT of them, cheap).
        results = []
        for segment, transcript, distance_value in rows:
            before = await self.session.scalar(
                select(Segment)
                .where(Segment.transcript_id == transcript.id, Segment.start_ms < segment.start_ms)
                .order_by(Segment.start_ms.desc())
                .limit(1)
            )
            after = await self.session.scalar(
                select(Segment)
                .where(Segment.transcript_id == transcript.id, Segment.start_ms > segment.start_ms)
                .order_by(Segment.start_ms.asc())
                .limit(1)
            )
            results.append(
                {
                    "segmentId": segment.id,
                    "transcriptId": transcript.id,
                    "transcriptTitle": transcript.title,
                    "speakerLabel": _speaker_label(segment.speaker),
                    "text": segment.text,
                    "contextBefore": before.text if before else None,
                    "contextAfter": after.text if after else None,
                    "startMs": segment.start_ms,
                    "timestampLabel": timestamp_label(segment.start_ms),
                    # Raw cosine distance is not an intuitive API surface; 1 - distance
                    # reads the way a "match strength" naturally should (higher = better).
                    "score": round(1 - float(distance_value), 4),
                }
            )

        return {"results": results}
