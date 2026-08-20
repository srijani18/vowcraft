"""Decisions — the cross-transcript reading surface for extracted decisions. SPEC-020.

Read-only by construction, like ``audit.py``: a ``Decision`` row has no status and no
correction path — ``services/extraction.py`` deletes and fully replaces a transcript's
decisions on every re-extraction — so there is no patch/bulk verb to add here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from vowcraft_db import Decision, Transcript

from app.domain.action_item import timestamp_label

_MAX_PAGE = 200


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class DecisionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _scoped(self, user_id: str):
        return (
            select(Decision)
            .join(Transcript, Transcript.id == Decision.transcript_id)
            .where(Transcript.user_id == user_id)
        )

    async def list_decisions(
        self,
        user_id: str,
        *,
        transcript_id: Optional[str] = None,
        decided_by: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        query = self._scoped(user_id).options(selectinload(Decision.transcript))
        if transcript_id:
            query = query.where(Decision.transcript_id == transcript_id)
        if decided_by and decided_by.strip():
            query = query.where(Decision.decided_by.ilike(decided_by.strip()))
        if search and search.strip():
            like = f"%{search.strip()}%"
            query = query.where(
                or_(Decision.statement.ilike(like), Decision.source_quote.ilike(like))
            )

        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))

        # Keyset on (createdAt, id), same rationale as AuditService: a Decision has no
        # priority-like column to sort on, and id breaks ties within one extraction call's
        # timestamp (every decision from one meeting is written in the same transaction).
        ordered = query.order_by(Decision.created_at_.desc(), Decision.id.desc())
        if cursor:
            anchor = (
                await self.session.execute(
                    select(Decision.created_at_, Decision.id).where(Decision.id == cursor)
                )
            ).first()
            if anchor is not None:
                cursor_at, cursor_id = anchor
                ordered = ordered.where(
                    or_(
                        Decision.created_at_ < cursor_at,
                        and_(Decision.created_at_ == cursor_at, Decision.id < cursor_id),
                    )
                )
        page_size = min(limit, _MAX_PAGE)
        rows = (await self.session.scalars(ordered.limit(page_size + 1))).all()
        has_more = len(rows) > page_size
        page = rows[:page_size] if has_more else rows

        # Facets from the unfiltered scope, same rationale as AuditService/ActionItemService:
        # the transcript picker should list every transcript the user has, not only ones
        # with a surviving match under the current filter.
        transcript_rows = (
            await self.session.scalars(
                select(Transcript)
                .where(Transcript.user_id == user_id)
                .order_by(Transcript.created_at_.desc())
                .limit(50)
            )
        ).all()
        decided_by_rows = (
            await self.session.scalars(
                select(Decision.decided_by)
                .join(Transcript, Transcript.id == Decision.transcript_id)
                .where(Transcript.user_id == user_id, Decision.decided_by.is_not(None))
                .distinct()
                .order_by(Decision.decided_by)
            )
        ).all()

        return {
            "decisions": [
                {
                    "id": r.id,
                    "statement": r.statement,
                    "decidedBy": r.decided_by,
                    "sourceTimestampMs": r.source_timestamp_ms,
                    "sourceTimestampLabel": timestamp_label(r.source_timestamp_ms),
                    "sourceQuote": r.source_quote,
                    "createdAt": _iso(r.created_at_),
                    "transcript": {
                        "id": r.transcript.id,
                        "title": r.transcript.title,
                        "recordedAt": _iso(r.transcript.recorded_at),
                    },
                }
                for r in page
            ],
            "total": total or 0,
            "facets": {
                "transcripts": [{"id": t.id, "title": t.title} for t in transcript_rows],
                "decidedBy": [d for d in decided_by_rows if d],
            },
            "nextCursor": page[-1].id if has_more and page else None,
        }
