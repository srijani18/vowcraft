"""The domain vocabulary — a port of ``src/domain/types.ts``.

Declared as plain string enums rather than imported from the ORM, exactly as the original
declares them independently of Prisma. That keeps this layer compilable and testable with
no database, no generated client, and no I/O — which is the property the whole guardrail
design rests on.

``RuleContext`` is literally "everything a rule may read". A rule that needs something not
in here is a rule that needs a design conversation, not an extra import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

ActionType = Literal["CALENDAR", "TASK", "EMAIL", "REMINDER", "NONE"]
Priority = Literal["HIGH", "MEDIUM", "LOW"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
ActionStatus = Literal[
    "PROPOSED", "APPROVED", "REJECTED", "DEFERRED", "EXECUTING", "EXECUTED", "FAILED"
]
RiskTier = Literal["LOW", "MEDIUM", "HIGH"]
ApprovalGate = Literal["AUTO", "EXPLICIT_APPROVAL", "EXPLICIT_APPROVAL_WITH_CONFIRMATION"]
Readiness = Literal["READY", "NEEDS_CLARIFICATION", "INFORMATIONAL"]
Severity = Literal["BLOCK", "WARN", "INFO"]


@dataclass
class ActionItemCore:
    id: str
    description: str
    action_type: ActionType
    status: ActionStatus
    priority: Priority
    confidence: Confidence
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    deadline: Optional[datetime] = None
    source_timestamp_ms: Optional[int] = None
    source_quote: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    superseded_by_id: Optional[str] = None
    depends_on_id: Optional[str] = None


@dataclass
class SettingsView:
    time_zone: str = "Asia/Kolkata"
    workday_start: str = "09:00"
    workday_end: str = "18:00"
    allow_weekends: bool = False
    max_meeting_minutes: int = 120
    min_buffer_minutes: int = 15
    org_domains: list[str] = field(default_factory=list)
    auto_execute_low_risk: bool = False
    budget_approval_limit: int = 1000
    org_currency: str = "USD"
    approval_thresholds: dict[str, str] = field(default_factory=dict)


@dataclass
class TeamMemberView:
    name: str
    email: str
    role: Optional[str] = None


@dataclass
class BusyBlockView:
    starts_at: datetime
    ends_at: datetime
    kind: str = "BUSY"
    title: Optional[str] = None


@dataclass
class DecisionView:
    statement: str
    decided_by: Optional[str] = None


@dataclass
class RuleContext:
    """Everything a rule may read, and nothing else.

    ``now`` is passed in rather than read from the clock. That is what makes every
    time-dependent rule testable against a fixed instant instead of whatever the machine
    happened to think the time was when the suite ran.
    """

    item: ActionItemCore
    payload: dict[str, Any]
    settings: SettingsView
    now: datetime
    team_members: list[TeamMemberView] = field(default_factory=list)
    busy_blocks: list[BusyBlockView] = field(default_factory=list)
    decisions: list[DecisionView] = field(default_factory=list)
    siblings: list[ActionItemCore] = field(default_factory=list)
    self_email: Optional[str] = None
    #: Whether a human has explicitly approved this item. Several policy rules block
    #: *until* that is true, so it is part of the rule input rather than something the
    #: caller checks afterwards — a check outside the engine is a check that can be
    #: forgotten (SPEC-003 §4).
    has_explicit_approval: bool = False


@dataclass(frozen=True)
class RuleViolation:
    rule_id: str
    severity: Severity
    message: str
    #: What the reviewer should do about it. Absent when the message says it already.
    remedy: Optional[str] = None


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: Severity
    applies_to: tuple[str, ...]
    evaluate: Any  # Callable[[RuleContext], Optional[RuleViolation]]
