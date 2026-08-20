"""Audit log reading — SPEC-003 §7.

Read-only by construction: there is no update or delete anywhere, and production grants the
app role INSERT/SELECT only. Writes go through ``AuditRepository.record``.

Scoped to the caller: the log holds every user's rows, so a query without the actor filter
would expose one tenant's activity to another. The scope is applied in one place for that
reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from voice2brd_db import ActionItem, AuditLog, Transcript, User

_MAX_PAGE = 200


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _scoped(self, user_id: str):
        """Rows this user is entitled to see.

        Either they were the actor, or the row concerns one of their action items. The
        second half matters because agent-written rows have the user as `actorId` but
        system-written ones may not.
        """
        owned_items = select(ActionItem.id).join(
            Transcript, Transcript.id == ActionItem.transcript_id
        ).where(Transcript.user_id == user_id)
        return select(AuditLog).where(
            or_(AuditLog.actor_id == user_id, AuditLog.action_item_id.in_(owned_items))
        )

    async def list_entries(
        self,
        user_id: str,
        *,
        events: Optional[list[str]] = None,
        action_item_id: Optional[str] = None,
        search: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        query = self._scoped(user_id)
        if events:
            query = query.where(AuditLog.event.in_(events))
        if action_item_id:
            query = query.where(AuditLog.action_item_id == action_item_id)
        if since:
            query = query.where(AuditLog.at >= since)
        if search and search.strip():
            like = f"%{search.strip()}%"
            query = query.outerjoin(ActionItem, ActionItem.id == AuditLog.action_item_id).where(
                or_(AuditLog.event.ilike(like), ActionItem.description.ilike(like))
            )

        total = await self.session.scalar(
            select(func.count()).select_from(query.subquery())
        )

        # Keyset (seek) pagination on `(at, id)`, not `OFFSET`: `id` breaks ties between
        # rows sharing a millisecond — a single transaction routinely writes more than
        # one audit row — and a seek is also stable under concurrent inserts, unlike
        # `OFFSET`, which can skip or repeat a row if the table changes between pages.
        ordered = query.order_by(AuditLog.at.desc(), AuditLog.id.desc())
        if cursor:
            anchor = (
                await self.session.execute(select(AuditLog.at, AuditLog.id).where(AuditLog.id == cursor))
            ).first()
            if anchor is not None:
                cursor_at, cursor_id = anchor
                ordered = ordered.where(
                    or_(AuditLog.at < cursor_at, and_(AuditLog.at == cursor_at, AuditLog.id < cursor_id))
                )
        page_size = min(limit, _MAX_PAGE)
        rows = (await self.session.scalars(ordered.limit(page_size + 1))).all()

        has_more = len(rows) > page_size
        page = rows[:page_size] if has_more else rows

        # Batched rather than joined per-row: the display fields (who, and what the
        # related action item says) are a lookup, not part of what selects or orders the
        # page, so two small `IN` queries beat pulling User/ActionItem into every row.
        actor_ids = {r.actor_id for r in page if r.actor_id}
        actors = (
            {u.id: u for u in (await self.session.scalars(select(User).where(User.id.in_(actor_ids)))).all()}
            if actor_ids
            else {}
        )
        action_item_ids = {r.action_item_id for r in page if r.action_item_id}
        descriptions = (
            dict(
                (
                    await self.session.execute(
                        select(ActionItem.id, ActionItem.description).where(ActionItem.id.in_(action_item_ids))
                    )
                ).all()
            )
            if action_item_ids
            else {}
        )

        def actor_label(actor_type: str, actor_id: Optional[str]) -> str:
            if actor_type == "SYSTEM":
                return "System"
            if actor_type == "AGENT":
                return "Agent"
            actor = actors.get(actor_id) if actor_id else None
            # `actorId` is `SET NULL` on user deletion, so a row can legitimately have no
            # actor left to name.
            return (actor.name or actor.email) if actor else "Deleted account"

        # Facet counts come from the *unfiltered* scope, so the filter chips still show
        # what else exists rather than only what is already selected.
        facet_rows = (
            await self.session.execute(
                select(AuditLog.event, func.count())
                .where(AuditLog.id.in_(select(self._scoped(user_id).subquery().c.id)))
                .group_by(AuditLog.event)
                .order_by(func.count().desc())
            )
        ).all()

        entries = []
        for r in page:
            actor_type = r.actor_type.value if hasattr(r.actor_type, "value") else str(r.actor_type)
            entries.append(
                {
                    "id": r.id,
                    "at": _iso(r.at),
                    "event": r.event,
                    "actorType": actor_type,
                    "actorLabel": actor_label(actor_type, r.actor_id),
                    "actionItemId": r.action_item_id,
                    "actionItemDescription": descriptions.get(r.action_item_id),
                    "before": r.before,
                    "after": r.after,
                    "metadata": r.metadata_,
                    "requestId": r.request_id,
                }
            )

        return {
            "entries": entries,
            "total": total or 0,
            "facets": {"events": [{"event": e, "count": c} for e, c in facet_rows]},
            "nextCursor": page[-1].id if has_more and page else None,
        }
