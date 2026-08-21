"""Segment embeddings for semantic search — SPEC-021.

One row per `Segment`, replaced wholesale on re-embed (delete-then-insert) rather than
updated in place — there is no partial-embedding state worth representing, the same
reasoning `services/extraction.py` already applies to `Decision` rows on re-extraction.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..columns import created_at, pk

if TYPE_CHECKING:
    from .transcript import Segment

#: voyage-3-lite's native output dimension (SPEC-021 §3). A model change that alters
#: dimensionality needs a new migration and a full re-embed — the column width is not
#: a formality that can just be widened.
EMBEDDING_DIM = 512


class SegmentEmbedding(Base):
    __tablename__ = "SegmentEmbedding"

    id: Mapped[str] = pk()
    segment_id: Mapped[str] = mapped_column(
        "segmentId", Text, ForeignKey("Segment.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    segment: Mapped["Segment"] = relationship(back_populates="embedding")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    # Recorded per row, matching Transcript.transcribeProvider/extractProvider — a future
    # model upgrade needs to know which rows are now stale.
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (
        Index("SegmentEmbedding_segmentId_key", "segmentId", unique=True),
        # HNSW, not IVFFlat: IVFFlat needs a representative sample of existing data to pick
        # good list counts and is only accurate after that training pass — wrong for a
        # table that starts empty. HNSW builds incrementally and needs no training step.
        Index(
            "SegmentEmbedding_embedding_hnsw_idx", "embedding",
            postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
