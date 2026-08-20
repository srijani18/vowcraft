"""Alembic environment — async engine, models from the package, URL from the environment.

Two decisions worth stating:

* **The URL is read from ``DATABASE_URL``**, never from ``alembic.ini``. Migrations run
  against production from a release job, and a file-committed URL is a credential leak
  waiting for a public repository.
* **The existing Prisma enum types are excluded from autogenerate.** This database was
  created by Prisma; SQLAlchemy would otherwise propose dropping and recreating all 15
  enum types on the first autogenerate, which would cascade to every column using them.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vowcraft_db.base import Base  # noqa: E402
import vowcraft_db.models  # noqa: E402,F401 — registers every mapper on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Migrations need it; alembic.ini deliberately does not carry one."
        )
    # Match the backend's normalisation so one variable serves both.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    if "?" in url:
        base, _, query = url.partition("?")
        keep = [
            p for p in query.split("&")
            if p and p.split("=")[0] not in {"schema", "sslmode", "pgbouncer", "connection_limit"}
        ]
        url = f"{base}?{'&'.join(keep)}" if keep else base
    return url


def include_object(obj, name, type_, reflected, compare_to):
    """Keep Prisma's artefacts out of autogenerate's diff.

    Enum types already exist and are shared by many columns; proposing to recreate them
    produces a migration that cannot run. The Prisma migrations table is likewise not
    ours to manage.
    """
    if type_ == "table" and name in {"_prisma_migrations"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
