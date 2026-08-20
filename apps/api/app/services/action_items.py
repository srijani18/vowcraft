"""Action items — the read model and the decision endpoint.

A port of ``src/server/action-items/``. The important property is that **every computed
field is computed here, server-side, on every read**: readiness, violations, risk tier,
approval gate, and whether execution is currently possible.

None of them is stored. A stored verdict can disagree with the row it describes — and would,
since the payload is editable — so a cached "READY" would survive an edit that removed a
required field. Recomputing costs one pure function call and cannot lie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from vowcraft_db import (
    ActionItem,
    CalendarBusyBlock,
    Correction,
    Decision,
    TeamMember,
    Transcript,
    UserSettings,
)

from app.core.config import Settings
from app.core.exceptions import bad_request, not_found, unprocessable
from app.domain.action_item import (
    AUDIT_EVENT_FOR_STATUS,
    GROUP_ORDER,
    SERVER_ONLY_STATUSES,
    compute_readiness,
    deadline_bucket,
    review_sort_key,
    timestamp_label,
    transition_error,
)
from app.domain.risk import RISK_META, classify_risk, effective_gate
from app.domain.rules import evaluate_rules
from app.domain.types import (
    ActionItemCore,
    BusyBlockView,
    DecisionView,
    RuleContext,
    SettingsView,
    TeamMemberView,
)
from app.integrations.registry import resolve_provider
from app.db.repositories.users import AuditRepository


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


#: Fields a human can correct — a port of CORRECTABLE in
#: src/server/action-items/service.ts. `status` and `confidence` are handled
#: separately: a status change follows the transition state machine rather than a
#: plain diff, and a confidence change is never treated as a "correction" worth
#: feeding back to the extractor.
CORRECTABLE_FIELDS: tuple[str, ...] = (
    "description",
    "owner_name",
    "owner_email",
    "deadline",
    "priority",
    "action_type",
    "payload",
)


def _scalar(value: Any) -> Any:
    """SQLAlchemy hands back an Enum member for an enum column; JSON storage (the audit
    and correction tables) and equality checks against a client-supplied plain string
    both need the bare value."""
    return value.value if hasattr(value, "value") else value


def _dt_to_iso(value: Optional[datetime]) -> Optional[str]:
    """Unlike `_iso`, safe for a value that may already carry a real offset — an incoming
    `deadline` from the client can, where every value `_iso` normally handles is a
    naive-but-UTC value read from the database."""
    if value is None:
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)).astimezone(
        timezone.utc
    ).isoformat()


def _values_equal(field: str, prev: Any, next_value: Any) -> bool:
    """A port of the `JSON.stringify(next) === JSON.stringify(prev)` check in
    patchActionItem — same intent (did this field actually change?), but comparing
    Python values directly rather than round-tripping through JSON text, so a `deadline`
    is compared as an instant rather than as two possibly-differently-offset strings."""
    if field == "deadline":
        if prev is None or next_value is None:
            return prev is None and next_value is None
        a = prev if prev.tzinfo is not None else prev.replace(tzinfo=timezone.utc)
        b = next_value if next_value.tzinfo is not None else next_value.replace(tzinfo=timezone.utc)
        return a.astimezone(timezone.utc) == b.astimezone(timezone.utc)
    if field == "payload":
        return (prev or {}) == (next_value or {})
    return _scalar(prev) == _scalar(next_value)


@dataclass
class EvaluationInputs:
    """Everything needed to evaluate any of the user's items.

    Loaded once per request rather than per item: a board of forty items would otherwise
    issue forty settings lookups and forty roster lookups.
    """

    settings: SettingsView
    self_email: str
    busy_blocks: list[BusyBlockView]
    team_members: list[TeamMemberView]
    decisions_by_transcript: dict[str, list[DecisionView]]
    provider_routing: dict[str, str]


def to_core(row: ActionItem) -> ActionItemCore:
    return ActionItemCore(
        id=row.id,
        description=row.description,
        action_type=row.action_type.value if hasattr(row.action_type, "value") else str(row.action_type),
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        priority=row.priority.value if hasattr(row.priority, "value") else str(row.priority),
        confidence=row.confidence.value if hasattr(row.confidence, "value") else str(row.confidence),
        owner_name=row.owner_name,
        owner_email=row.owner_email,
        deadline=row.deadline,
        source_timestamp_ms=row.source_timestamp_ms,
        source_quote=row.source_quote,
        payload=row.payload if isinstance(row.payload, dict) else {},
        superseded_by_id=row.superseded_by_id,
        depends_on_id=row.depends_on_id,
    )


def _blocked_reason(
    core: ActionItemCore, blocking: list, has_provider: bool, readiness: str
) -> Optional[str]:
    """Why execution is unavailable, in the order the reviewer should hear it.

    Status first, because "approve this first" is more useful than a guardrail message on an
    item nobody has looked at yet.
    """
    if core.status == "EXECUTED":
        return "Already executed."
    if core.status == "EXECUTING":
        return "Execution in progress."
    if core.status not in ("APPROVED", "FAILED"):
        return "Approve this action first."
    if readiness == "INFORMATIONAL":
        return "Nothing to execute for this item."
    if blocking:
        return blocking[0].message
    if not has_provider:
        return f"No integration is configured to handle a {core.action_type} action."
    return None


def evaluate(core: ActionItemCore, inputs: EvaluationInputs, transcript_id: Optional[str], now: datetime):
    ctx = RuleContext(
        item=core,
        payload=core.payload,
        settings=inputs.settings,
        now=now,
        team_members=inputs.team_members,
        busy_blocks=inputs.busy_blocks,
        decisions=inputs.decisions_by_transcript.get(transcript_id or "", []),
        self_email=inputs.self_email,
        # APPROVED or FAILED means a human has looked at it and said yes. FAILED counts
        # because a retry of an approved-then-failed item should not need re-approving.
        has_explicit_approval=core.status in ("APPROVED", "FAILED"),
    )
    rules = evaluate_rules(ctx)
    readiness = compute_readiness(core, rules.violations)
    risk = classify_risk(core, core.payload, inputs.settings, inputs.self_email)
    gate = effective_gate(risk.tier, core.confidence, inputs.settings, core.action_type)
    provider = resolve_provider(core.action_type, inputs.provider_routing)
    return rules, readiness, risk, gate, provider


def to_dto(row: ActionItem, inputs: EvaluationInputs, now: datetime) -> dict[str, Any]:
    core = to_core(row)
    rules, readiness, risk, gate, provider = evaluate(core, inputs, row.transcript_id, now)
    blocked = _blocked_reason(core, rules.blocking, provider is not None, readiness.readiness)

    transcript = getattr(row, "transcript", None)
    superseded = getattr(row, "superseded_by", None)
    blocker = getattr(row, "depends_on", None)

    return {
        "id": row.id,
        "description": row.description,
        "actionType": core.action_type,
        "status": core.status,
        "priority": core.priority,
        "confidence": core.confidence,
        "ownerName": row.owner_name,
        "ownerEmail": row.owner_email,
        "deadline": _iso(row.deadline),
        "sourceTimestampMs": row.source_timestamp_ms,
        "sourceTimestampLabel": timestamp_label(row.source_timestamp_ms),
        "sourceQuote": row.source_quote,
        "reasoning": row.reasoning,
        "payload": core.payload,
        "executionResult": row.execution_result,
        "executionAttempts": row.execution_attempts,
        "executedAt": _iso(row.executed_at),
        "provider": row.provider,
        "createdAt": _iso(row.created_at_),
        "updatedAt": _iso(row.updated_at_),
        # ── computed, authoritative, never stored
        "readiness": readiness.readiness,
        "missingFields": readiness.missing_fields,
        "violations": [
            {"ruleId": v.rule_id, "severity": v.severity, "message": v.message, "remedy": v.remedy}
            for v in rules.violations
        ],
        "riskTier": risk.tier,
        "riskFactors": risk.factors,
        "riskLabel": RISK_META[risk.tier]["label"],
        "approvalGate": gate.gate,
        "gateNote": gate.note,
        "providerId": provider.id if provider else None,
        "canExecute": blocked is None,
        "blockedReason": blocked,
        "transcript": (
            {"id": transcript.id, "title": transcript.title, "recordedAt": _iso(transcript.recorded_at)}
            if transcript
            else None
        ),
        "supersededBy": (
            {"id": superseded.id, "description": superseded.description} if superseded else None
        ),
        "dependsOn": (
            {"id": blocker.id, "description": blocker.description, "status": _scalar(blocker.status)}
            if blocker
            else None
        ),
    }


class ActionItemService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def load_inputs(self, user_id: str, user_email: str) -> EvaluationInputs:
        settings_row = await self.session.scalar(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        team_rows = (
            await self.session.scalars(select(TeamMember).where(TeamMember.user_id == user_id))
        ).all()
        busy_rows = (
            await self.session.scalars(
                select(CalendarBusyBlock).where(CalendarBusyBlock.user_id == user_id)
            )
        ).all()
        decision_rows = (
            await self.session.scalars(
                select(Decision)
                .join(Transcript, Transcript.id == Decision.transcript_id)
                .where(Transcript.user_id == user_id)
            )
        ).all()

        decisions: dict[str, list[DecisionView]] = {}
        for row in decision_rows:
            decisions.setdefault(row.transcript_id, []).append(
                DecisionView(statement=row.statement, decided_by=row.decided_by)
            )

        view = SettingsView()
        if settings_row is not None:
            view = SettingsView(
                time_zone=settings_row.time_zone,
                workday_start=settings_row.workday_start,
                workday_end=settings_row.workday_end,
                allow_weekends=settings_row.allow_weekends,
                max_meeting_minutes=settings_row.max_meeting_minutes,
                min_buffer_minutes=settings_row.min_buffer_minutes,
                org_domains=list(settings_row.org_domains or []),
                auto_execute_low_risk=settings_row.auto_execute_low_risk,
                budget_approval_limit=settings_row.budget_approval_limit,
                org_currency=settings_row.org_currency,
                approval_thresholds=dict(settings_row.approval_thresholds or {}),
            )

        return EvaluationInputs(
            settings=view,
            self_email=user_email,
            busy_blocks=[
                BusyBlockView(
                    starts_at=b.starts_at.replace(tzinfo=timezone.utc),
                    ends_at=b.ends_at.replace(tzinfo=timezone.utc),
                    kind=b.kind.value if hasattr(b.kind, "value") else str(b.kind),
                    title=b.title,
                )
                for b in busy_rows
            ],
            team_members=[TeamMemberView(name=t.name, email=t.email, role=t.role) for t in team_rows],
            decisions_by_transcript=decisions,
            provider_routing=dict((settings_row.provider_routing if settings_row else None) or {}),
        )

    def _base_query(self, user_id: str):
        return (
            select(ActionItem)
            .join(Transcript, Transcript.id == ActionItem.transcript_id)
            .where(Transcript.user_id == user_id)
            .options(
                selectinload(ActionItem.transcript),
                # `to_dto` reads `superseded_by`/`depends_on` for the DTO's `supersededBy`/
                # `dependsOn` fields. Any item flagged by POL_SUPERSEDED or carrying a
                # dependency has one of these set, and without eager loading it here too,
                # reading it is an implicit lazy load — the same MissingGreenlet trap as
                # the transcript relationship, just on a rarer path.
                selectinload(ActionItem.superseded_by),
                selectinload(ActionItem.depends_on),
            )
        )

    async def list_items(
        self,
        user_id: str,
        user_email: str,
        *,
        status: Optional[list[str]] = None,
        priority: Optional[list[str]] = None,
        action_type: Optional[list[str]] = None,
        readiness: Optional[list[str]] = None,
        owner: Optional[str] = None,
        deadline: Optional[str] = None,
        transcript_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """A port of ``src/server/action-items/service.ts::listActionItems``.

        One deliberate simplification: Prisma paginates at the database with a
        `take: limit + 1` cursor, so `nextCursor` there reflects raw rows that exist
        *before* the readiness/deadline filters run — a "load more" can legitimately
        fetch a page that filters down to nothing. `readiness` and `deadline` are computed,
        not columns, so pushing this into SQL exactly would mean replicating Postgres's
        enum declaration order for `priority` by hand. Filtering, then sorting, then
        paginating over the *final* list is simpler, cannot drift from the enum's real
        order, and "load more" always yields visible rows when it reports there are more —
        arguably the more correct behaviour, not just an easier one.
        """
        query = self._base_query(user_id)
        if status:
            query = query.where(ActionItem.status.in_(status))
        if priority:
            query = query.where(ActionItem.priority.in_(priority))
        if action_type:
            query = query.where(ActionItem.action_type.in_(action_type))
        if transcript_id:
            query = query.where(ActionItem.transcript_id == transcript_id)
        if owner:
            query = query.where(ActionItem.owner_name.ilike(owner.strip()))
        if search:
            like = f"%{search.strip()}%"
            query = query.where(
                or_(ActionItem.description.ilike(like), ActionItem.source_quote.ilike(like))
            )

        rows = (await self.session.scalars(query)).all()
        inputs = await self.load_inputs(user_id, user_email)
        now = datetime.now(timezone.utc)

        triples = [(row, to_core(row), to_dto(row, inputs, now)) for row in rows]
        triples.sort(key=lambda t: review_sort_key(t[1]))

        if readiness:
            triples = [t for t in triples if t[2]["readiness"] in readiness]
        if deadline:
            triples = [t for t in triples if deadline_bucket(t[0].deadline, now) == deadline]

        start = 0
        if cursor:
            for idx, (row, _core, _dto) in enumerate(triples):
                if row.id == cursor:
                    start = idx + 1
                    break
            else:
                # An unrecognised cursor (the item was deleted, or belongs to another
                # filter view) yields an empty page rather than an error or a restart
                # from the top, which would silently duplicate rows already shown.
                start = len(triples)

        page = triples[start : start + limit]
        has_more = start + limit < len(triples)

        items = [dto for _row, _core, dto in page]
        counts: dict[str, Any] = {group: 0 for group in GROUP_ORDER}
        counts["total"] = len(items)
        for dto in items:
            counts[dto["readiness"]] = counts.get(dto["readiness"], 0) + 1

        owner_rows = (
            await self.session.scalars(
                select(ActionItem.owner_name)
                .join(Transcript, Transcript.id == ActionItem.transcript_id)
                .where(Transcript.user_id == user_id, ActionItem.owner_name.is_not(None))
                .distinct()
                .order_by(ActionItem.owner_name)
            )
        ).all()
        transcript_rows = (
            await self.session.scalars(
                select(Transcript)
                .where(Transcript.user_id == user_id)
                .order_by(Transcript.created_at_.desc())
                .limit(50)
            )
        ).all()

        return {
            "items": items,
            "counts": counts,
            "facets": {
                "owners": [name for name in owner_rows if name],
                "transcripts": [{"id": t.id, "title": t.title} for t in transcript_rows],
            },
            "nextCursor": page[-1][0].id if has_more and page else None,
        }

    async def get_item(self, user_id: str, user_email: str, item_id: str) -> dict[str, Any]:
        row = await self.session.scalar(self._base_query(user_id).where(ActionItem.id == item_id))
        if row is None:
            # 404 rather than 403 for another user's row: a 403 confirms it exists, which
            # turns id-guessing into an enumeration oracle.
            raise not_found("That action item does not exist.")
        inputs = await self.load_inputs(user_id, user_email)
        return to_dto(row, inputs, datetime.now(timezone.utc))

    async def patch_item(
        self,
        user_id: str,
        user_email: str,
        item_id: str,
        *,
        status: Optional[str] = None,
        fields: Optional[dict[str, Any]] = None,
        confidence: Optional[str] = None,
        decision_note: Optional[str] = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        """A port of ``src/server/action-items/service.ts::patchActionItem``.

        ``fields`` carries only the correctable keys the client actually sent (snake_case
        already — see the route), so a value explicitly set to ``null`` and a value never
        mentioned are both simply absent from the dict and never compared. TS makes the
        same simplification implicitly by only ever calling this from two real call
        sites: `Board.tsx`'s status-only `decide()`, and `EditModal.tsx`, which always
        sends every correctable field with an explicit value or `null` — never a partial
        subset — so "omitted" and "explicitly cleared" never need to be told apart here.
        """
        fields = fields or {}
        row = await self.session.scalar(self._base_query(user_id).where(ActionItem.id == item_id))
        if row is None:
            raise not_found("That action item does not exist.")

        current = _scalar(row.status)

        if status is not None and status != current:
            # State-machine validity first, "is this specific status client-assignable"
            # second: a status can be *both* the right next step and one the executor
            # alone may assign (APPROVED → EXECUTING is exactly this), and only that
            # combination is `server_only_status` rather than a plain illegal transition.
            # Checking the reverse order made every PATCH naming EXECUTING or EXECUTED
            # report `server_only_status` even from a status that could never legally
            # reach it at all (PROPOSED → EXECUTED, say) — a real transition-shape
            # question misreported as a permissions one.
            problem = transition_error(current, status)  # type: ignore[arg-type]
            if problem:
                raise unprocessable("illegal_transition", problem)
            if status in SERVER_ONLY_STATUSES:
                raise unprocessable(
                    "server_only_status",
                    f"{status} is set by the executor, not by a client.",
                )

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        # (field, before, after) for each actually-changed correctable field — kept
        # separate from the audit's before/after because a Correction row is per field,
        # not per request, and excludes status/confidence (see CORRECTABLE_FIELDS).
        corrections: list[tuple[str, Any, Any]] = []

        for field in CORRECTABLE_FIELDS:
            if field not in fields:
                continue
            next_value = fields[field]
            prev_value = getattr(row, field)
            if _values_equal(field, prev_value, next_value):
                continue
            setattr(row, field, next_value)
            prev_json = _dt_to_iso(prev_value) if field == "deadline" else _scalar(prev_value)
            next_json = _dt_to_iso(next_value) if field == "deadline" else _scalar(next_value)
            before[field] = prev_json
            after[field] = next_json
            corrections.append((field, prev_json, next_json))

        # A human edit is a stronger signal than the extractor's own guess, so an
        # explicit confidence change is honoured but never inferred from an edit, and
        # never recorded as a Correction (it is not something the extractor got wrong).
        if confidence is not None and confidence != _scalar(row.confidence):
            before["confidence"] = _scalar(row.confidence)
            row.confidence = confidence  # type: ignore[assignment]
            after["confidence"] = confidence

        status_changed = status is not None and status != current
        if status_changed:
            before["status"] = current
            row.status = status  # type: ignore[assignment]
            after["status"] = status

        if not before:
            # Nothing actually changed — no write, no audit row.
            inputs = await self.load_inputs(user_id, user_email)
            return to_dto(row, inputs, datetime.now(timezone.utc))

        excerpt = row.source_quote

        for field, before_value, after_value in corrections:
            self.session.add(
                Correction(
                    action_item_id=row.id,
                    field=field,
                    before_value=before_value,
                    after_value=after_value,
                    transcript_excerpt=excerpt,
                    corrected_by=user_id,
                )
            )

        # One audit row per PATCH, whatever combination of fields changed: a status
        # decision if there was one, otherwise an edit. SPEC-003 §10 criterion 4 depends
        # on exactly one row per change — writing one per touched field here would
        # double- or triple-count a single PATCH against that invariant.
        event = (AUDIT_EVENT_FOR_STATUS.get(status) if status_changed else None) or "action_item.edited"
        audit = AuditRepository(self.session)
        await audit.record(
            event=event,
            actor_id=user_id,
            action_item_id=row.id,
            request_id=request_id,
            before=before,
            after=after,
            metadata={"note": decision_note} if decision_note else None,
        )

        await self.session.flush()
        # `updatedAt` is computed by the database (`onupdate=func.now()`), so after a flush
        # SQLAlchemy marks it expired; reading it later is an implicit lazy load, which an
        # async session cannot do outside an explicit await and raises MissingGreenlet.
        #
        # `refresh(row, [...])` looked like the fix but is not one here: passing specific
        # attribute names limits which *columns* it re-fetches, but it still expires the
        # whole instance first — including `transcript`, which was eagerly loaded by
        # `_base_query`'s `selectinload` and is now unloaded again. `to_dto` then touches
        # `row.transcript` and hits the same error one line later. Re-running the full query
        # both re-fetches `updatedAt` and re-applies the eager load in one step.
        row = await self.session.scalar(
            self._base_query(user_id).where(ActionItem.id == item_id)
        )

        inputs = await self.load_inputs(user_id, user_email)
        return to_dto(row, inputs, datetime.now(timezone.utc))

    #: A port of OP_STATUS in src/server/action-items/service.ts.
    BULK_OP_STATUS: dict[str, str] = {"approve": "APPROVED", "reject": "REJECTED", "defer": "DEFERRED"}

    async def bulk_update(
        self,
        user_id: str,
        user_email: str,
        ids: list[str],
        op: str,
        request_id: str,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """A port of ``src/server/action-items/service.ts::bulkUpdate``.

        Per-item outcomes, not all-or-nothing: a caller selecting twelve items and
        approving them wants the ten that can move to move. Failing the batch because two
        are superseded would make bulk actions useless exactly when they are most wanted.
        """
        status = self.BULK_OP_STATUS[op]
        results: list[dict[str, Any]] = []

        for item_id in ids:
            try:
                await self.patch_item(
                    user_id, user_email, item_id, status=status, decision_note=note, request_id=request_id
                )
                results.append({"id": item_id, "ok": True})
            except Exception as exc:  # noqa: BLE001 — reported per item, see the docstring
                message = getattr(exc, "message", None) or str(exc)
                results.append({"id": item_id, "ok": False, "error": message})

        return {
            "results": results,
            "okCount": sum(1 for r in results if r["ok"]),
            "failedCount": sum(1 for r in results if not r["ok"]),
        }
