"""State machine and readiness — a port of ``src/domain/action-item.ts``.

``readiness`` is **derived, never stored**. That is deliberate: a stored flag can disagree
with the row it describes, and this one would — the payload is editable, so a stored
"READY" survives an edit that removes a required field. Recomputing costs nothing and
cannot lie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.domain.payload import field_label, missing_fields
from app.domain.types import ActionItemCore, ActionStatus, Readiness, RuleViolation

_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "PROPOSED": ("APPROVED", "REJECTED", "DEFERRED"),
    "DEFERRED": ("APPROVED", "REJECTED", "PROPOSED"),
    "APPROVED": ("EXECUTING", "REJECTED", "PROPOSED"),
    "EXECUTING": ("EXECUTED", "FAILED"),
    "FAILED": ("EXECUTING", "REJECTED"),
    # Terminal. Undoing an executed action means a *compensating* action, not a status edit
    # — the calendar invite has already been sent and the database cannot unsend it.
    "EXECUTED": (),
    "REJECTED": ("PROPOSED",),
}

#: Statuses a client may never request. The executor owns them; accepting them from a
#: request would let a caller mark something EXECUTED without anything having executed.
SERVER_ONLY_STATUSES: tuple[str, ...] = ("EXECUTING", "EXECUTED")

AUDIT_EVENT_FOR_STATUS: dict[str, str] = {
    "APPROVED": "action_item.approved",
    "REJECTED": "action_item.rejected",
    "DEFERRED": "action_item.deferred",
    "PROPOSED": "action_item.reopened",
}


def can_transition(from_status: ActionStatus, to_status: ActionStatus) -> bool:
    return to_status in _TRANSITIONS.get(from_status, ())


def transition_error(from_status: ActionStatus, to_status: ActionStatus) -> Optional[str]:
    """Why a transition is refused, in words a reviewer can act on.

    A no-op is allowed rather than rejected: a client re-sending the current status is
    idempotent, not an error.
    """
    if from_status == to_status:
        return None
    if from_status == "EXECUTED":
        return (
            "This action has already been executed. Undo it with a compensating action "
            "instead."
        )
    if not can_transition(from_status, to_status):
        return f"Cannot move an action from {from_status} to {to_status}."
    return None


@dataclass(frozen=True)
class ReadinessResult:
    readiness: Readiness
    missing_fields: list[str]
    reasons: list[str]


def compute_readiness(
    item: ActionItemCore, violations: Optional[list[RuleViolation]] = None
) -> ReadinessResult:
    violations = violations or []

    if item.action_type == "NONE" or item.status == "REJECTED":
        return ReadinessResult(
            readiness="INFORMATIONAL",
            missing_fields=[],
            reasons=(
                ["Rejected by a reviewer."]
                if item.status == "REJECTED"
                else ["No executable action."]
            ),
        )

    missing = missing_fields(item.action_type, item.payload)
    blocking = [v for v in violations if v.severity == "BLOCK"]

    reasons: list[str] = []
    if missing:
        labels = [field_label(item.action_type, key) for key in missing]
        noun = "detail" if len(labels) == 1 else "details"
        reasons.append(f"Missing required {noun}: {', '.join(labels)}.")
    # Low confidence and a missing owner are reasons to ask a human to look, even when
    # every required field is present — matching src/domain/action-item.ts exactly. An
    # earlier port dropped both checks, which would have shown a low-confidence or
    # unowned item as READY (executable) rather than NEEDS_CLARIFICATION.
    if item.confidence == "LOW":
        reasons.append("The extractor had low confidence in this item.")
    if not item.owner_name:
        reasons.append("No owner identified.")
    reasons.extend(v.message for v in blocking)

    readiness: Readiness = "NEEDS_CLARIFICATION" if reasons else "READY"
    return ReadinessResult(readiness=readiness, missing_fields=missing, reasons=reasons)


#: Board ordering. Not alphabetical: it is the order a reviewer should work in — things
#: that can be acted on now, then things that need a decision, then the rest.
GROUP_ORDER: tuple[str, ...] = ("READY", "NEEDS_CLARIFICATION", "INFORMATIONAL")

_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def priority_rank(priority: str) -> int:
    return _PRIORITY_RANK.get(priority, 1)


def is_in_execution_lane(status: ActionStatus) -> bool:
    """Whether the item is on its way to a provider, so the UI must not offer edits."""
    return status in ("EXECUTING", "EXECUTED")


def _as_utc(value: datetime) -> datetime:
    """The database stores naive timestamps that are UTC by convention (see `_iso` in
    `services/action_items.py`); a naive `datetime.timestamp()`/subtraction otherwise
    assumes the *server's local* zone, which would silently mis-order or mis-bucket
    deadlines depending on where the process happens to run."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


#: A port of src/domain/action-item.ts::compareForReview. Fixed order — priority, then
#: deadline (nulls last), then where in the recording it was said — not user-configurable
#: on purpose: a stable order is what makes "work top to bottom" correct, and reviewers
#: lose their place when the order shifts under them.
def review_sort_key(item: ActionItemCore) -> tuple[int, float, int]:
    deadline_key = _as_utc(item.deadline).timestamp() if item.deadline else float("inf")
    return (priority_rank(item.priority), deadline_key, item.source_timestamp_ms or 0)


#: A port of src/lib/time.ts::deadlineBucket.
def deadline_bucket(deadline: Optional[datetime], now: datetime) -> str:
    if deadline is None:
        return "none"
    diff = _as_utc(deadline) - _as_utc(now)
    if diff.total_seconds() < 0:
        return "overdue"
    if diff <= timedelta(days=1):
        return "today"
    if diff <= timedelta(days=7):
        return "week"
    return "none"
