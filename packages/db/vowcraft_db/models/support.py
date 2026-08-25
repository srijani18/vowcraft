"""Audit log, integrations, credentials, busy blocks, and BRD documents."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ARRAY, Boolean, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..columns import created_at, enum_column, pk, ts, updated_at
from ..enums import ActorType, BrdStatus, BusyKind, CredentialModule, CredentialStatus


class AuditLog(Base):
    """Append-only — SPEC-003 §7.

    There is deliberately no update or delete path anywhere in the codebase, and
    production grants the app role INSERT/SELECT only.

    The two foreign keys behave differently, and both match the live schema:

    * ``actorId`` is ``SET NULL`` — deleting a user must not erase the record of what
      they did.
    * ``actionItemId`` is ``SET NULL`` — deleting an action item detaches its audit rows
      and keeps them. This was ``CASCADE`` until action items became deletable from the
      UI: an audit trail that cascades is weaker than one that does not, and a delete
      button turned that from a latent inconsistency into a single click that erased the
      record of what had been proposed, approved and executed. The row survives with a
      null ``actionItemId``; ``event``, ``before``/``after``, ``metadata`` and ``at`` are
      all still readable.
    """

    __tablename__ = "AuditLog"

    id: Mapped[str] = pk()
    at: Mapped[datetime] = created_at("at")
    actor_type: Mapped[ActorType] = enum_column(
        ActorType, name="ActorType", column_name="actorType", server_default=text("'USER'")
    )
    actor_id: Mapped[Optional[str]] = mapped_column(
        "actorId", Text, ForeignKey("User.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    # Dotted and past tense: `action_item.executed`, `brd.revised`.
    event: Mapped[str] = mapped_column(Text, nullable=False)
    action_item_id: Mapped[Optional[str]] = mapped_column(
        "actionItemId", Text, ForeignKey("ActionItem.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column("requestId", Text, nullable=True)

    __table_args__ = (
        Index("AuditLog_actionItemId_at_idx", "actionItemId", "at"),
        Index("AuditLog_event_at_idx", "event", "at"),
    )


class IntegrationAccount(Base):
    """A connected third-party account — SPEC-002 §4.

    Tokens are stored encrypted (``*Enc``) via the same AES-256-GCM envelope as BYOK
    secrets. ``needsReauth`` is set when a refresh fails, so the UI can ask for a
    reconnect instead of failing every execution silently.
    """

    __tablename__ = "IntegrationAccount"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_id: Mapped[Optional[str]] = mapped_column("externalAccountId", Text, nullable=True)
    account_label: Mapped[Optional[str]] = mapped_column("accountLabel", Text, nullable=True)
    # Nullable to match the live schema (see UserSettings.org_domains).
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("ARRAY[]::text[]"), nullable=True
    )
    access_token_enc: Mapped[Optional[str]] = mapped_column("accessTokenEnc", Text, nullable=True)
    refresh_token_enc: Mapped[Optional[str]] = mapped_column("refreshTokenEnc", Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = ts("expiresAt")
    needs_reauth: Mapped[bool] = mapped_column(
        "needsReauth", Boolean, server_default=text("false"), nullable=False
    )
    connected_at: Mapped[datetime] = created_at("connectedAt")
    updated_at_: Mapped[datetime] = updated_at()

    __table_args__ = (
        Index("IntegrationAccount_userId_provider_key", "userId", "provider", unique=True),
    )


class Credential(Base):
    """A BYOK provider secret — SPEC-004.

    ``secretsEnc`` is the whole secret set as one encrypted JSON envelope, AAD-bound to
    ``cred:<userId>:<service>``. ``hints`` holds masked previews only — enough to
    recognise which key is stored, never enough to use it.
    """

    __tablename__ = "Credential"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    module: Mapped[CredentialModule] = enum_column(CredentialModule, name="CredentialModule")
    service: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    secrets_enc: Mapped[str] = mapped_column("secretsEnc", Text, nullable=False)
    hints: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    status: Mapped[CredentialStatus] = enum_column(
        CredentialStatus, name="CredentialStatus", server_default=text("'UNVERIFIED'")
    )
    last_verified_at: Mapped[Optional[datetime]] = ts("lastVerifiedAt")
    last_error: Mapped[Optional[str]] = mapped_column("lastError", Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at_: Mapped[datetime] = created_at()
    updated_at_: Mapped[datetime] = updated_at()

    __table_args__ = (
        Index("Credential_userId_service_key", "userId", "service", unique=True),
        Index("Credential_userId_module_idx", "userId", "module"),
    )


class CalendarBusyBlock(Base):
    """Busy time used by SCHED_CONFLICT / SCHED_DND without calling a provider."""

    __tablename__ = "CalendarBusyBlock"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = ts("startsAt", nullable=False)
    ends_at: Mapped[datetime] = ts("endsAt", nullable=False)
    kind: Mapped[BusyKind] = enum_column(BusyKind, name="BusyKind", server_default=text("'BUSY'"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'seed'"), nullable=False)

    __table_args__ = (Index("CalendarBusyBlock_userId_startsAt_idx", "userId", "startsAt"),)


class BrdDocument(Base):
    """A business requirements document dictated by voice — SPEC-014 §4.

    ``content`` is the *current* document, denormalised so listing and reading never
    replay revisions. Its shape is the §5 tool schema as JSON, not markdown: markdown is
    a rendering, and storing prose would force refinement to parse its own previous
    output back into structure.
    """

    __tablename__ = "BrdDocument"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # DRAFTING with revisions but no content is an interrupted session, which is
    # resumable rather than garbage (SPEC-014 §4.3).
    status: Mapped[BrdStatus] = enum_column(BrdStatus, name="BrdStatus", server_default=text("'DRAFTING'"))
    content: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column("lastError", Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()
    updated_at_: Mapped[datetime] = updated_at()

    revisions: Mapped[list["BrdRevision"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="BrdRevision.ordinal"
    )

    __table_args__ = (Index("BrdDocument_userId_createdAt_idx", "userId", "createdAt"),)


class BrdRevision(Base):
    """One speaking turn and what it did — SPEC-014 §4.1, §6.

    ``content`` is the *full* document as of this revision, not a diff: any point in
    history is readable without reconstruction, and one malformed entry cannot break the
    chain.
    """

    __tablename__ = "BrdRevision"

    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(
        "documentId", Text, ForeignKey("BrdDocument.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    document: Mapped[BrdDocument] = relationship(back_populates="revisions")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # This turn alone — the answer to "what did I say that caused this change?". The
    # accumulation is derivable; the per-turn text is not.
    spoken_text: Mapped[str] = mapped_column("spokenText", Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[Optional[str]] = mapped_column("changeSummary", Text, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (
        Index("BrdRevision_documentId_ordinal_key", "documentId", "ordinal", unique=True),
        Index("BrdRevision_documentId_ordinal_idx", "documentId", "ordinal"),
    )
