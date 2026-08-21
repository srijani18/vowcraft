"""Shared fixtures.

``NOW`` is a fixed instant, not ``datetime.now()``. Every time-dependent rule takes its
"now" as an argument precisely so the suite can be deterministic — a test that passes in the
morning and fails at 18:01 because the workday ended is worse than no test.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Set before any app module is imported: `Settings` is read at import time in places, and a
# missing DATABASE_URL would fail collection rather than a test.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-any-deployment")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("APP_ENV", "test")
# Explicit rather than relying on Settings' own "mock" default: a test environment
# should never depend on a default staying what it is today (see app/core/config.py's
# note on this field — it was briefly the wrong default in this exact code).
os.environ.setdefault("INTEGRATIONS_MODE", "mock")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from vowcraft_db import (  # noqa: E402
    ActionItem,
    Base,
    Decision,
    Segment,
    SegmentEmbedding,
    Speaker,
    Transcript,
    TranscriptAsset,
    User,
)

from app.core.config import Settings  # noqa: E402
from app.domain.types import (  # noqa: E402
    ActionItemCore,
    BusyBlockView,
    DecisionView,
    RuleContext,
    SettingsView,
    TeamMemberView,
)

# A database dedicated to this suite, never the one `docker-compose.yml`'s `web`/`api`
# serve real accounts from — created once with:
#   psql -U vowcraft -d postgres -c "CREATE DATABASE vowcraft_test OWNER vowcraft;"
#   DATABASE_URL=postgresql://vowcraft:vowcraft@localhost:5432/vowcraft_test \
#     python -m alembic -c packages/db/alembic.ini upgrade head
TEST_DATABASE_URL = os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+asyncpg://vowcraft:vowcraft@localhost:5432/vowcraft_test"
)

#: Thursday, 09:00 UTC. Chosen so a weekday/weekend rule has an unambiguous answer and the
#: workday boundaries are far enough away to test either side deliberately.
NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings() -> SettingsView:
    return SettingsView(
        time_zone="UTC",
        workday_start="09:00",
        workday_end="17:30",
        allow_weekends=False,
        max_meeting_minutes=120,
        min_buffer_minutes=15,
        org_domains=["acme.com"],
        auto_execute_low_risk=False,
        budget_approval_limit=1000,
        org_currency="USD",
        approval_thresholds={},
    )


@pytest.fixture
def team() -> list[TeamMemberView]:
    return [TeamMemberView(name="Priya Raman", email="priya@acme.com")]


@pytest.fixture
def busy() -> list[BusyBlockView]:
    return [
        BusyBlockView(
            starts_at=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
            kind="BUSY",
            title="Standup",
        ),
        BusyBlockView(
            starts_at=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc),
            kind="LUNCH",
            title="Lunch",
        ),
    ]


@pytest.fixture
def decisions() -> list[DecisionView]:
    return [DecisionView(statement="We will ship the invoicing module in October")]


@pytest.fixture
def make_item():
    def _make(action_type: str = "CALENDAR", **overrides) -> ActionItemCore:
        base = dict(
            id="item-1",
            description="Hold a review",
            action_type=action_type,
            status="PROPOSED",
            priority="MEDIUM",
            confidence="HIGH",
            payload={},
        )
        base.update(overrides)
        return ActionItemCore(**base)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def make_ctx(settings, team, busy, decisions):
    def _make(item: ActionItemCore, *, approved: bool = False, **overrides) -> RuleContext:
        ctx = RuleContext(
            item=item,
            payload=item.payload,
            settings=settings,
            now=NOW,
            team_members=team,
            busy_blocks=busy,
            decisions=decisions,
            self_email="me@acme.com",
            has_explicit_approval=approved,
        )
        for key, value in overrides.items():
            setattr(ctx, key, value)
        return ctx

    return _make


# ──────────────────────────────────────────── database-backed fixtures ──
#
# Everything below runs against the real Postgres schema (enums, JSONB, constraints,
# cascades) rather than a mock session — the class of bug this migration keeps finding
# (MissingGreenlet, wrong defaults, cascade direction) only shows up against a real
# engine. Every test gets its own transaction, rolled back at teardown: application code
# calls `session.commit()` internally (the routes do, to close the audit-row boundary),
# and `join_transaction_mode="create_savepoint"` turns each of those into a savepoint
# release rather than a real commit, so the outer rollback still undoes it all. Nothing
# here ever touches the `vowcraft` database real accounts live in.


@pytest.fixture
async def db_engine():
    # Function-scoped, matching `asyncio_default_fixture_loop_scope = "function"": each
    # test gets its own event loop, and an asyncpg connection created on one loop breaks
    # ("another operation is in progress") the moment a later test's loop reuses it. A
    # session-scoped engine is the faster shape but only survives a session-scoped loop.
    engine = create_async_engine(TEST_DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with session_factory() as session:
            yield session
        await trans.rollback()


@pytest.fixture
def api_settings() -> Settings:
    return Settings()


@pytest.fixture
def make_user(db_session):
    async def _make(email: str = "reviewer@acme.test", **overrides) -> User:
        row = User(email=email, **overrides)
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_transcript(db_session):
    async def _make(user: User, title: str = "Sprint planning", **overrides) -> Transcript:
        row = Transcript(user_id=user.id, title=title, **overrides)
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_speaker(db_session):
    async def _make(transcript: Transcript, label: str = "Speaker 1", **overrides) -> Speaker:
        row = Speaker(transcript_id=transcript.id, label=label, **overrides)
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_segment(db_session):
    async def _make(
        transcript: Transcript, *, start_ms: int = 0, end_ms: int = 1000,
        text: str = "Hello there.", speaker: Optional[Speaker] = None, **overrides,
    ) -> Segment:
        row = Segment(
            transcript_id=transcript.id, start_ms=start_ms, end_ms=end_ms, text=text,
            speaker_id=speaker.id if speaker else None, **overrides,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_segment_embedding(db_session):
    async def _make(
        segment: Segment, *, embedding: Optional[list[float]] = None,
        provider: str = "voyage", model: str = "voyage-3-lite", **overrides,
    ) -> SegmentEmbedding:
        row = SegmentEmbedding(
            segment_id=segment.id, embedding=embedding or [0.0] * 512,
            provider=provider, model=model, **overrides,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_decision(db_session):
    async def _make(transcript: Transcript, **overrides) -> Decision:
        base = dict(
            transcript_id=transcript.id,
            statement="We will ship the invoicing module in October",
            decided_by="Priya Raman",
            source_timestamp_ms=1000,
            source_quote="let's ship invoicing in October",
        )
        base.update(overrides)
        row = Decision(**base)
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_asset(db_session):
    async def _make(
        transcript: Transcript, *, filename: str = "recording.mp3",
        mime_type: str = "audio/mpeg", data: bytes = b"fake-audio-bytes", **overrides,
    ) -> TranscriptAsset:
        row = TranscriptAsset(
            transcript_id=transcript.id, filename=filename, mime_type=mime_type,
            byte_size=len(data), checksum="test-checksum", data=data, **overrides,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_row(db_session):
    """An `ActionItem` row, complete enough to be READY by default so a test that only
    cares about one axis (e.g. pagination) does not also have to think about readiness."""

    async def _make(transcript: Transcript, **overrides) -> ActionItem:
        base = dict(
            transcript_id=transcript.id,
            description="Send the recap email",
            action_type="EMAIL",
            status="PROPOSED",
            priority="MEDIUM",
            confidence="HIGH",
            owner_name="Priya Raman",
            payload={"to": ["priya@acme.test"], "subject": "Recap", "body": "See notes."},
        )
        base.update(overrides)
        row = ActionItem(**base)
        db_session.add(row)
        await db_session.flush()
        return row

    return _make
