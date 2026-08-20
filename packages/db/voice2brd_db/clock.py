"""Timestamps that survive a round trip through the database.

Every timestamp column in this schema is ``timestamp(3)`` — millisecond precision — and
Postgres **rounds** rather than truncates when storing a finer value. Python's
``datetime.now()`` produces microseconds, so a value written and then read back differs
about half the time, in the last millisecond.

That is harmless for a ``createdAt`` and severe for ``passwordUpdatedAt``, which is
compared exactly: every session token carries it as the ``pwd`` claim, and a token issued
from the pre-write value fails verification against the rounded stored value. The symptom
is an intermittent, unreproducible logout immediately after signing in — with nothing in
the logs to distinguish it from a genuine revocation.

The TypeScript implementation never hit this because a JavaScript ``Date`` is natively
millisecond-precision, so its writes were already exact.

So: truncate to whole milliseconds *before* writing. What goes in is then what comes out.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_ms() -> datetime:
    """Current UTC time, truncated to whole milliseconds, naive.

    Naive because the columns are ``timestamp without time zone`` and asyncpg refuses an
    aware datetime for them. Truncated — not rounded — so the value is always representable
    in the column and never rounds *up* into a different millisecond.
    """
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000, tzinfo=None)


def to_epoch_ms(value: datetime | None) -> int:
    """Milliseconds since the epoch, treating a naive datetime as UTC.

    Explicit about UTC rather than relying on ``datetime.timestamp()``, which interprets a
    naive value in the *system* timezone. That would work as long as every process agreed,
    and would invalidate every outstanding token the moment one container's TZ differed
    from another's — a failure that appears only after a deploy and only for some users.
    """
    if value is None:
        return 0
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(aware.timestamp() * 1000)
