"""Action items, execution attempts, approvals, corrections — the workflow cluster."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .transcript import Transcript

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..columns import created_at, enum_column, pk, ts, updated_at
from ..enums import (
    ActionStatus,
    ActionType,
    ApprovalGate,
    ApprovalState,
    AttemptOutcome,
    Confidence,
    Priority,
    RiskTier,
)


class ActionItem(Base):
    """One thing the meeting committed to — SPEC-001.

    The self-referential columns encode three different relationships, deliberately kept
    separate rather than collapsed into one "related item" field: supersession (a later
    item replaces this one), hierarchy (subtasks), and ordering (this cannot run until
    that has). They answer different questions and the guardrails read them differently.
    """

    __tablename__ = "ActionItem"

    id: Mapped[str] = pk()
    transcript_id: Mapped[str] = mapped_column(
        "transcriptId", Text, ForeignKey("Transcript.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    # Needed by the board, which shows which recording each item came from and links the
    # citation back into the reader. A relationship rather than a second query per item.
    transcript: Mapped["Transcript"] = relationship(back_populates="action_items")

    description: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[ActionType] = enum_column(ActionType, name="ActionType", column_name="actionType", server_default=text("'NONE'"))
    owner_name: Mapped[Optional[str]] = mapped_column("ownerName", Text, nullable=True)
    owner_email: Mapped[Optional[str]] = mapped_column("ownerEmail", Text, nullable=True)
    deadline: Mapped[Optional[datetime]] = ts("deadline")
    priority: Mapped[Priority] = enum_column(Priority, name="Priority", server_default=text("'MEDIUM'"))
    confidence: Mapped[Confidence] = enum_column(Confidence, name="Confidence", server_default=text("'MEDIUM'"))

    # ── grounding: why this item exists at all (SPEC-010 §7)
    source_timestamp_ms: Mapped[Optional[int]] = mapped_column("sourceTimestampMs", Integer, nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column("sourceQuote", Text, nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Provider-shaped and deliberately allowed to be incomplete: a missing field is what
    # makes an item NEEDS_CLARIFICATION rather than a validation error at extraction time.
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    status: Mapped[ActionStatus] = enum_column(ActionStatus, name="ActionStatus", server_default=text("'PROPOSED'"))
    execution_result: Mapped[Optional[dict]] = mapped_column("executionResult", JSONB, nullable=True)
    execution_attempts: Mapped[int] = mapped_column(
        "executionAttempts", Integer, server_default=text("0"), nullable=False
    )
    executed_at: Mapped[Optional[datetime]] = ts("executedAt")
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    superseded_by_id: Mapped[Optional[str]] = mapped_column(
        "supersededById", Text, ForeignKey("ActionItem.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    superseded_by: Mapped[Optional["ActionItem"]] = relationship(
        remote_side=[id], foreign_keys=[superseded_by_id], backref="supersedes"
    )

    # CASCADE, matching the live schema: deleting a parent removes its subtasks, since a
    # subtask with no parent has no meaning in the workflow. Supersession and dependency,
    # below, are SET NULL — losing a *pointer* is survivable, and the pointed-at item is
    # still a real item on its own.
    parent_id: Mapped[Optional[str]] = mapped_column(
        "parentId", Text, ForeignKey("ActionItem.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True
    )
    step_order: Mapped[Optional[int]] = mapped_column("stepOrder", Integer, nullable=True)
    depends_on_id: Mapped[Optional[str]] = mapped_column(
        "dependsOnId", Text, ForeignKey("ActionItem.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    # The dependency gate (SPEC-002 §8) reads `depends_on_id` directly and needs no eager
    # load for that; this relationship exists so the DTO can also tell a reviewer *what*
    # it is blocked on, not just that it is.
    depends_on: Mapped[Optional["ActionItem"]] = relationship(
        remote_side=[id], foreign_keys=[depends_on_id], backref="blocks"
    )

    created_at_: Mapped[datetime] = created_at()
    updated_at_: Mapped[datetime] = updated_at()

    attempts: Mapped[list["ExecutionAttempt"]] = relationship(
        back_populates="action_item", cascade="all, delete-orphan"
    )
    approvals: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="action_item", cascade="all, delete-orphan"
    )
    corrections: Mapped[list["Correction"]] = relationship(
        back_populates="action_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ActionItem_transcriptId_status_idx", "transcriptId", "status"),
        Index("ActionItem_status_priority_deadline_idx", "status", "priority", "deadline"),
        Index("ActionItem_ownerName_idx", "ownerName"),
    )


class ExecutionAttempt(Base):
    """One dispatch attempt — SPEC-002 §5, §10.

    ``idempotencyKey`` is unique across the table, which is what makes execution
    exactly-once: a replayed request finds the prior attempt instead of dispatching
    again. The uniqueness lives in the database rather than in application logic because
    two concurrent requests would otherwise both pass an application-level check.
    """

    __tablename__ = "ExecutionAttempt"

    id: Mapped[str] = pk()
    action_item_id: Mapped[str] = mapped_column(
        "actionItemId", Text, ForeignKey("ActionItem.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    action_item: Mapped[ActionItem] = relationship(back_populates="attempts")

    # NOT globally unique: uniqueness is (actionItemId, idempotencyKey, attemptNumber).
    # A retry of the same logical dispatch reuses the key and increments the attempt
    # number, so a global constraint would reject attempt 2 of every retried execution
    # and break the retry policy outright (SPEC-002 §10).
    idempotency_key: Mapped[str] = mapped_column("idempotencyKey", Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column("attemptNumber", Integer, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # "mock" or "live" — recorded per attempt, so an audit reader can tell whether a
    # historical execution actually reached the outside world.
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[AttemptOutcome] = enum_column(
        AttemptOutcome, name="AttemptOutcome", server_default=text("'RUNNING'")
    )
    external_id: Mapped[Optional[str]] = mapped_column("externalId", Text, nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column("externalUrl", Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column("errorCode", Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column("errorMessage", Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column("durationMs", Integer, nullable=True)
    started_at: Mapped[datetime] = created_at("startedAt")
    finished_at: Mapped[Optional[datetime]] = ts("finishedAt")

    __table_args__ = (
        Index(
            "ExecutionAttempt_actionItemId_idempotencyKey_attemptNumber_key",
            "actionItemId", "idempotencyKey", "attemptNumber", unique=True,
        ),
        Index("ExecutionAttempt_idempotencyKey_outcome_idx", "idempotencyKey", "outcome"),
    )


class ApprovalRequest(Base):
    """The gate a risky item must pass — SPEC-003 §5.

    ``presented`` snapshots exactly what the approver was shown. Without it, "they
    approved it" is unfalsifiable: the payload may have changed afterwards, and an audit
    that cannot reconstruct the decision is not an audit.
    """

    __tablename__ = "ApprovalRequest"

    id: Mapped[str] = pk()
    action_item_id: Mapped[str] = mapped_column(
        "actionItemId", Text, ForeignKey("ActionItem.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    action_item: Mapped[ActionItem] = relationship(back_populates="approvals")

    risk_tier: Mapped[RiskTier] = enum_column(RiskTier, name="RiskTier", column_name="riskTier")
    required_gate: Mapped[ApprovalGate] = enum_column(
        ApprovalGate, name="ApprovalGate", column_name="requiredGate"
    )
    state: Mapped[ApprovalState] = enum_column(
        ApprovalState, name="ApprovalState", server_default=text("'PENDING'")
    )
    approver_id: Mapped[Optional[str]] = mapped_column(
        "approverId", Text, ForeignKey("User.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = ts("decidedAt")
    decision_note: Mapped[Optional[str]] = mapped_column("decisionNote", Text, nullable=True)
    presented: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = ts("expiresAt", nullable=False)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (Index("ApprovalRequest_state_expiresAt_idx", "state", "expiresAt"),)


class Correction(Base):
    """A human edit, kept as training signal — SPEC-001 §10.

    Stored per field rather than as a whole-row diff so a future prompt-tuning pass can
    ask "which field does the extractor get wrong most often?" and get an answer.
    """

    __tablename__ = "Correction"

    id: Mapped[str] = pk()
    action_item_id: Mapped[str] = mapped_column(
        "actionItemId", Text, ForeignKey("ActionItem.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    action_item: Mapped[ActionItem] = relationship(back_populates="corrections")
    field: Mapped[str] = mapped_column(Text, nullable=False)
    before_value: Mapped[Optional[dict]] = mapped_column("beforeValue", JSONB, nullable=True)
    after_value: Mapped[Optional[dict]] = mapped_column("afterValue", JSONB, nullable=True)
    transcript_excerpt: Mapped[Optional[str]] = mapped_column("transcriptExcerpt", Text, nullable=True)
    corrected_by: Mapped[Optional[str]] = mapped_column("correctedBy", Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()

    # Indexed by field, because the question this table exists to answer is "which field
    # does the extractor get wrong most often, and is it improving?".
    __table_args__ = (Index("Correction_field_createdAt_idx", "field", "createdAt"),)
