"""Transcript, speakers, segments, words, decisions, and the stored audio."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .action import ActionItem
    from .embedding import SegmentEmbedding

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, LargeBinary, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..columns import created_at, enum_column, pk, ts
from ..enums import TranscriptSource, TranscriptStatus


class Transcript(Base):
    __tablename__ = "Transcript"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[TranscriptSource] = enum_column(
        TranscriptSource, name="TranscriptSource", column_name="sourceType",
        server_default=text("'UPLOAD'"),
    )
    source_uri: Mapped[Optional[str]] = mapped_column("sourceUri", Text, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column("durationMs", Integer, nullable=True)
    recorded_at: Mapped[Optional[datetime]] = ts("recordedAt")
    status: Mapped[TranscriptStatus] = enum_column(
        TranscriptStatus, name="TranscriptStatus", server_default=text("'READY'")
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()

    # ── pipeline state, SPEC-010 §5. Kept on the row so a reload does not lose
    # progress and a restart leaves a diagnosable state.
    stage: Mapped[str] = mapped_column(Text, server_default=text("'done'"), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, server_default=text("100"), nullable=False)
    # Recorded per stage, so a failure says which half broke and the other half's work
    # is not thrown away.
    transcribe_error: Mapped[Optional[str]] = mapped_column("transcribeError", Text, nullable=True)
    extract_error: Mapped[Optional[str]] = mapped_column("extractError", Text, nullable=True)
    # The machine-readable reason alongside the human sentence — the UI branches on it,
    # because "add a key" and "the model does not exist" have different fixes
    # (SPEC-010 §3.4).
    extract_error_code: Mapped[Optional[str]] = mapped_column("extractErrorCode", Text, nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column("extractModel", Text, nullable=True)
    transcribed_at: Mapped[Optional[datetime]] = ts("transcribedAt")
    extracted_at: Mapped[Optional[datetime]] = ts("extractedAt")
    transcribe_provider: Mapped[Optional[str]] = mapped_column("transcribeProvider", Text, nullable=True)
    extract_provider: Mapped[Optional[str]] = mapped_column("extractProvider", Text, nullable=True)
    # False when no provider supplied speaker labels — the UI says "speakers not
    # separated" rather than implying a single speaker (SPEC-010 §6).
    diarized: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    segments: Mapped[list["Segment"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="Segment.start_ms"
    )
    speakers: Mapped[list["Speaker"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )
    action_items: Mapped[list["ActionItem"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )
    asset: Mapped[Optional["TranscriptAsset"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("Transcript_userId_createdAt_idx", "userId", "createdAt"),
        Index("Transcript_userId_status_idx", "userId", "status"),
    )


class Speaker(Base):
    __tablename__ = "Speaker"

    id: Mapped[str] = pk()
    transcript_id: Mapped[str] = mapped_column(
        "transcriptId", Text, ForeignKey("Transcript.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    transcript: Mapped[Transcript] = relationship(back_populates="speakers")
    # The machine's label ("Speaker 1"). Kept even after a rename, so the mapping back
    # to the diarizer's output stays legible.
    label: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column("displayName", Text, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    segments: Mapped[list["Segment"]] = relationship(back_populates="speaker")

    # One label per transcript: the diarizer's "Speaker 1" must not appear twice.
    __table_args__ = (
        Index("Speaker_transcriptId_label_key", "transcriptId", "label", unique=True),
    )


class Segment(Base):
    __tablename__ = "Segment"

    id: Mapped[str] = pk()
    transcript_id: Mapped[str] = mapped_column(
        "transcriptId", Text, ForeignKey("Transcript.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    transcript: Mapped[Transcript] = relationship(back_populates="segments")
    # SetNull rather than Cascade: deleting a speaker must not delete what they said.
    speaker_id: Mapped[Optional[str]] = mapped_column(
        "speakerId", Text, ForeignKey("Speaker.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    speaker: Mapped[Optional[Speaker]] = relationship(back_populates="segments")
    start_ms: Mapped[int] = mapped_column("startMs", Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column("endMs", Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    words: Mapped[list["Word"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", order_by="Word.start_ms"
    )
    embedding: Mapped[Optional["SegmentEmbedding"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (Index("Segment_transcriptId_startMs_idx", "transcriptId", "startMs"),)


class Word(Base):
    """Word-level timings, for the reader's highlight — SPEC-012 §3."""

    __tablename__ = "Word"

    id: Mapped[str] = pk()
    segment_id: Mapped[str] = mapped_column(
        "segmentId", Text, ForeignKey("Segment.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    segment: Mapped[Segment] = relationship(back_populates="words")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column("startMs", Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column("endMs", Integer, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("Word_segmentId_startMs_idx", "segmentId", "startMs"),)


class Decision(Base):
    """Something settled in the meeting that is not itself an action."""

    __tablename__ = "Decision"

    id: Mapped[str] = pk()
    transcript_id: Mapped[str] = mapped_column(
        "transcriptId", Text, ForeignKey("Transcript.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    transcript: Mapped[Transcript] = relationship(back_populates="decisions")
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[Optional[str]] = mapped_column("decidedBy", Text, nullable=True)
    source_timestamp_ms: Mapped[Optional[int]] = mapped_column("sourceTimestampMs", Integer, nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column("sourceQuote", Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (Index("Decision_transcriptId_idx", "transcriptId"),)


class TranscriptAsset(Base):
    """The uploaded bytes — SPEC-010 §4.

    Held in the database rather than on a filesystem or in object storage: at 25 MB it
    keeps the deployment to one stateful service and makes the upload transactional with
    its Transcript row. Object storage is the obvious swap at scale.
    """

    __tablename__ = "TranscriptAsset"

    id: Mapped[str] = pk()
    transcript_id: Mapped[str] = mapped_column(
        "transcriptId", Text, ForeignKey("Transcript.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    transcript: Mapped[Transcript] = relationship(back_populates="asset")
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column("mimeType", Text, nullable=False)
    byte_size: Mapped[int] = mapped_column("byteSize", Integer, nullable=False)
    # sha-256 of the bytes. Re-uploading identical audio reuses the transcript instead
    # of spending the free tier twice (SPEC-010 §5).
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (
        Index("TranscriptAsset_transcriptId_key", "transcriptId", unique=True),
        Index("TranscriptAsset_checksum_idx", "checksum"),
    )
