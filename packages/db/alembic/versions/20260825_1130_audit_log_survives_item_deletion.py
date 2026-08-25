"""audit log survives action item deletion

`AuditLog.actionItemId` was `ON DELETE CASCADE`, so removing an action item removed the
record of what had been proposed, approved, and executed with it. That is the opposite of
what this table claims to be: SPEC-003 §7 calls it append-only, production grants the app
role INSERT/SELECT only, and `actorId` is already `SET NULL` precisely so deleting a *user*
cannot erase what they did.

The model's own docstring flagged this as a deliberate weakness and said tightening it would
need "a data-retention argument behind it, not a quiet edit here". Adding a user-facing
delete button for action items is that argument: it converts a latent inconsistency into an
active data-loss path that a single click would take.

After this, deleting an item detaches its audit rows (`actionItemId` becomes null) and keeps
them. `metadata`, `before`/`after`, `event` and `at` are all still there, so the trail
remains readable — it simply no longer points at a row that no longer exists.

Revision ID: b7c1d9e42a10
Revises: 83690e83dde3
Created: 2026-08-25 11:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = 'b7c1d9e42a10'
down_revision: Union[str, None] = '83690e83dde3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Named explicitly rather than looked up: Prisma's generated name is stable, and an
#: `ALTER TABLE ... DROP CONSTRAINT IF EXISTS` on a guessed name would silently no-op and
#: leave CASCADE in place — the failure mode being fixed.
_CONSTRAINT = "AuditLog_actionItemId_fkey"


def upgrade() -> None:
    op.execute(f'ALTER TABLE "AuditLog" DROP CONSTRAINT IF EXISTS "{_CONSTRAINT}"')
    op.execute(
        f'ALTER TABLE "AuditLog" ADD CONSTRAINT "{_CONSTRAINT}" '
        'FOREIGN KEY ("actionItemId") REFERENCES "ActionItem"(id) '
        'ON DELETE SET NULL ON UPDATE CASCADE'
    )


def downgrade() -> None:
    # Reverting restores the data-loss behaviour, which is the point of a downgrade being
    # honest: it is here to make the migration reversible, not because CASCADE is wanted.
    op.execute(f'ALTER TABLE "AuditLog" DROP CONSTRAINT IF EXISTS "{_CONSTRAINT}"')
    op.execute(
        f'ALTER TABLE "AuditLog" ADD CONSTRAINT "{_CONSTRAINT}" '
        'FOREIGN KEY ("actionItemId") REFERENCES "ActionItem"(id) '
        'ON DELETE CASCADE ON UPDATE CASCADE'
    )
