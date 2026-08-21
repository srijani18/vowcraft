"""add tab capture source

Revision ID: 83690e83dde3
Revises: 6f0a4f6bba2b
Created: 2026-08-21 19:16:18.313839
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '83690e83dde3'
down_revision: Union[str, None] = '6f0a4f6bba2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres allows adding an enum value inside a transaction as long as the new value
    # isn't *used* in that same transaction — this migration only adds it.
    op.execute("ALTER TYPE \"TranscriptSource\" ADD VALUE IF NOT EXISTS 'TAB_CAPTURE'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for an enum type — removing one requires rebuilding the
    # type from scratch, which is destructive if any row already uses it. Left in place on
    # downgrade, matching this migration's own predecessor's choice not to drop the
    # `vector` extension it added: additive is safe to leave, cheap to ignore.
    pass
