"""add segment embeddings

Revision ID: 6f0a4f6bba2b
Revises: b292dd67bc2d
Created: 2026-08-21 18:14:15.143602
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = '6f0a4f6bba2b'
down_revision: Union[str, None] = 'b292dd67bc2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS: safe to run against a database another process (or a prior partial
    # run) already extended — ensure_migration_state.py already documents this database's
    # dual-writer reality (Prisma's `db push` and Alembic both touch it).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Guarded per object, not as one all-or-nothing block, because the table and its
    # indexes can genuinely arrive separately. `schema.prisma` carries a shadow
    # `SegmentEmbedding` model (so `db push` does not drop it — see SPEC-021 §4), which
    # means a Prisma push can create the *table* while being unable to express the HNSW
    # index at all. Skipping the indexes just because the table exists would leave semantic
    # search on a sequential scan: correct results, quietly terrible latency.
    #
    # Idempotency here is load-bearing, not defensive: ensure_migration_state.py stamps the
    # baseline and lets `upgrade head` replay everything after it, so this migration runs
    # again on any database Prisma has already shaped.
    bind = op.get_bind()
    if sa.inspect(bind).has_table('SegmentEmbedding'):
        _ensure_indexes()
        return

    op.create_table(
        'SegmentEmbedding',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('segmentId', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(512), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('createdAt', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['segmentId'], ['Segment.id'],
            name=op.f('SegmentEmbedding_segmentId_fkey'), onupdate='CASCADE', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('SegmentEmbedding_pkey')),
    )
    _ensure_indexes()


def _ensure_indexes() -> None:
    """Both indexes, `IF NOT EXISTS`, in raw SQL.

    `op.create_index` cannot emit `IF NOT EXISTS`, and the HNSW one cannot be expressed by
    Prisma at all, so this is the only form that is safe to re-run against a database that
    may already have the table, one index, or both.
    """
    op.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS "SegmentEmbedding_segmentId_key" '
        'ON "SegmentEmbedding" ("segmentId")'
    )
    # The table is empty at migration time (a brand-new feature), so a plain index build
    # is instant — no need for the autocommit dance `CONCURRENTLY` would otherwise require.
    op.execute(
        'CREATE INDEX IF NOT EXISTS "SegmentEmbedding_embedding_hnsw_idx" '
        'ON "SegmentEmbedding" USING hnsw (embedding vector_cosine_ops)'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS "SegmentEmbedding_embedding_hnsw_idx"')
    op.execute('DROP INDEX IF EXISTS "SegmentEmbedding_segmentId_key"')
    op.execute('DROP TABLE IF EXISTS "SegmentEmbedding"')
    # Deliberately not dropping the `vector` extension: cheap to leave installed, and
    # dropping it would break anything else that has come to depend on it since.
