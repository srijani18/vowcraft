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
    op.create_index('SegmentEmbedding_segmentId_key', 'SegmentEmbedding', ['segmentId'], unique=True)
    # The table is empty at migration time (a brand-new feature), so a plain index build
    # is instant — no need for the autocommit dance `CONCURRENTLY` would otherwise require.
    op.create_index(
        'SegmentEmbedding_embedding_hnsw_idx', 'SegmentEmbedding', ['embedding'],
        unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    op.drop_index('SegmentEmbedding_embedding_hnsw_idx', table_name='SegmentEmbedding')
    op.drop_index('SegmentEmbedding_segmentId_key', table_name='SegmentEmbedding')
    op.drop_table('SegmentEmbedding')
    # Deliberately not dropping the `vector` extension: cheap to leave installed, and
    # dropping it would break anything else that has come to depend on it since.
