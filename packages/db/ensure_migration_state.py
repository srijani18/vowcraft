"""Run before ``alembic upgrade head`` on every container start — SPEC-015 §3.

``prisma db push --accept-data-loss`` (the Next.js entrypoint, ``docker/entrypoint.sh``)
resyncs the database to match ``schema.prisma`` on every boot, and Prisma has no notion of
``alembic_version`` — it isn't in its schema. Whether that push actively drops the table or
just never recreates it, the observed effect is the same: any ``web`` restart after an
``api`` restart leaves ``alembic_version`` missing while every real table Prisma manages
is still present and correct.

Alembic's own ``upgrade head`` cannot tell "this database was never migrated" apart from
"this database already has everything, just not the tracking row" — it assumes the former
and tries to run the baseline migration's DDL from scratch, which fails with "already
exists" the moment two services share one database this way.

This script tells them apart the same way a human would when debugging this by hand
(exactly what happened the first two times this surfaced): if the tracking table is
missing but the schema's own tables are already there, the database is not unmigrated —
it is unstamped. Stamp it, and let ``upgrade head`` do its normal, actually-idempotent
thing immediately after.
"""

from __future__ import annotations

import asyncio
import os
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


async def _existing_tables(database_url: str) -> set[str]:
    # No sync Postgres driver (psycopg2/psycopg) is installed — only asyncpg, same as the
    # app itself — so the reflection has to go through an async engine too.
    url = database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    finally:
        await engine.dispose()


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; letting `alembic upgrade head` fail with its own message.")
        return

    tables = asyncio.run(_existing_tables(database_url))

    if "alembic_version" in tables:
        return  # Already tracked — `upgrade head` will be the normal no-op.

    # Baseline migration's own first table (see the migration file) — present only if the
    # schema was already created by something else (Prisma).
    if "OAuthState" not in tables:
        return  # Genuinely a fresh database; `upgrade head` should create everything.

    print(
        "→ alembic_version is missing but the schema already exists "
        "(a Prisma `db push` on the other service almost certainly dropped it) "
        "— stamping the current revision instead of re-running its DDL"
    )
    config = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    command.stamp(config, "head")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — never block startup over this check
        print(f"ensure_migration_state.py: {exc}", file=sys.stderr)
