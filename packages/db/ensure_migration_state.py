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

**It stamps the baseline, not head, and that distinction is the whole point.** Stamping
head marks every migration as applied without running any of them, so a database that was
recreated by Prisma silently misses everything added after the baseline. That shipped: a
migration altering ``AuditLog``'s foreign key was stamped over and never ran, leaving the
schema disagreeing with the models with no error anywhere to say so. Stamping the baseline
— the revision Prisma's ``db push`` actually reproduces, since ``schema.prisma`` is the
baseline's own snapshot — leaves ``upgrade head`` to replay each later migration for real.

The invariant that makes this safe: **every migration after the baseline must be
idempotent**, because this path will re-run it against a database Prisma has already
shaped. Use ``IF NOT EXISTS`` / ``IF EXISTS``, or guard on a reflected check. A migration
that fails on second run will block every container start.
"""

from __future__ import annotations

import asyncio
import os
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
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

    config = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))

    # The base revision, read from the scripts rather than hardcoded, so adding or renaming
    # migrations cannot leave a stale id here.
    bases = ScriptDirectory.from_config(config).get_bases()
    if len(bases) != 1:
        # Multiple bases mean a branched history, where "the revision Prisma reproduces" is
        # no longer a single answer. Refusing beats guessing: `upgrade head` will then fail
        # loudly with its own message rather than this script stamping the wrong thing.
        print(
            f"→ expected exactly one base revision, found {len(bases)}; "
            "leaving the database unstamped for `alembic upgrade head` to report"
        )
        return
    baseline = bases[0]

    print(
        "→ alembic_version is missing but the schema already exists "
        "(a Prisma `db push` on the other service almost certainly dropped it) "
        f"— stamping the baseline ({baseline}) so `upgrade head` replays everything after it"
    )
    command.stamp(config, baseline)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — never block startup over this check
        print(f"ensure_migration_state.py: {exc}", file=sys.stderr)
