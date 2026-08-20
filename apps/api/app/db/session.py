"""Async engine and session factory.

``expire_on_commit=False`` is deliberate: with it on, every attribute read after a commit
triggers a fresh SELECT, which in an async session raises ``MissingGreenlet`` rather than
lazily loading. Turning it off means a service can commit and then serialise the object it
just wrote, which is what almost every route does.

Pool sizing assumes Render's managed Postgres, where connection limits are low on smaller
plans. A pool larger than the instance allows fails at the worst time — under load.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    _engine = create_async_engine(
        settings.DATABASE_URL,
        # Verify a connection before handing it out: managed Postgres and PgBouncer both
        # drop idle connections, and without this the first request after an idle period
        # fails with a stale-connection error.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        echo=False,
    )
    _session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("init_engine() must be called during startup before sessions are requested.")
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request, rolled back on any unhandled exception.

    The rollback matters: without it a failed request can leave the connection in a
    poisoned transaction, and the *next* request to borrow it from the pool fails for no
    visible reason.
    """
    async with session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
