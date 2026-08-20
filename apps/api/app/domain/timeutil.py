"""Pure time helpers the rules depend on — a port of ``src/lib/time.ts`` and
``src/domain/rules/helpers.ts``.

Kept in the domain package rather than a general ``lib``: every function is pure and takes
its "now" as an argument, and moving them next to a module that reads a clock or a database
would quietly break the guarantee that rules are testable at a fixed instant.

``zoneinfo`` rather than manual offset arithmetic — an hour offset computed by hand is
wrong twice a year, and "wrong on the DST boundary" is exactly the bug nobody reproduces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


def read_date(value: Any) -> Optional[datetime]:
    """Accept the shapes a payload date arrives in: datetime, epoch ms, or ISO string."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def read_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def read_string(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _zone(time_zone: str) -> ZoneInfo:
    try:
        return ZoneInfo(time_zone)
    except Exception:  # noqa: BLE001 — an unknown zone must not break the whole engine
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class ZonedParts:
    #: 0 = Sunday, matching the JavaScript convention the original used, so weekday
    #: comparisons and label lookups port without an off-by-one.
    weekday: int
    hour: int
    minute: int
    minutes_into_day: int


def zoned_parts(instant: datetime, time_zone: str) -> ZonedParts:
    local = instant.astimezone(_zone(time_zone))
    # Python's Monday=0 to JavaScript's Sunday=0.
    weekday = (local.weekday() + 1) % 7
    return ZonedParts(
        weekday=weekday,
        hour=local.hour,
        minute=local.minute,
        minutes_into_day=local.hour * 60 + local.minute,
    )


def is_weekend(instant: datetime, time_zone: str) -> bool:
    return zoned_parts(instant, time_zone).weekday in (0, 6)


def minutes_into_day(instant: datetime, time_zone: str) -> int:
    return zoned_parts(instant, time_zone).minutes_into_day


def parse_clock(value: str) -> Optional[int]:
    """"HH:MM" to minutes past midnight. None when unparseable."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def overlaps(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """Half-open intervals, so back-to-back events do not count as a clash.

    A meeting ending at 10:00 and one starting at 10:00 do not overlap. But a *point*
    (a reminder, where start == end) falling inside a window does coincide with it, which
    is why the comparison is not simply strict on both sides.
    """
    if a_start == a_end:
        return b_start <= a_start < b_end
    if b_start == b_end:
        return a_start <= b_start < a_end
    return a_start < b_end and b_start < a_end


def gap_minutes(earlier_end: datetime, later_start: datetime) -> float:
    return (later_start - earlier_end).total_seconds() / 60


_WEEKDAY_LABEL = (
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
)


def weekday_name(index: int) -> str:
    return _WEEKDAY_LABEL[index] if 0 <= index < 7 else "that day"


def human_time(instant: datetime, time_zone: str) -> str:
    """Matches the original's ``en-GB`` 24-hour format: "Fri 22 Aug, 14:30".

    Formatted here rather than in the UI because the string appears inside rule messages,
    which are stored in the audit log — so the reviewer and the record agree.
    """
    local = instant.astimezone(_zone(time_zone))
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, {local.strftime('%H:%M')}"


def scheduled_window(
    action_type: str, payload: dict[str, Any]
) -> Optional[tuple[datetime, datetime]]:
    """When the action happens. A reminder is a point; a meeting is a span.

    Defaults an absent duration to 30 minutes rather than returning nothing: the rules that
    care about a window should still run on an item whose duration was not stated, and
    ``VAL_REQUIRED_FIELDS`` is what reports the omission.
    """
    if action_type == "REMINDER":
        at = read_date(payload.get("remindAt"))
        return (at, at) if at else None
    start = read_date(payload.get("startsAt"))
    if not start:
        return None
    minutes = read_number(payload.get("durationMinutes")) or 30
    return start, start + timedelta(minutes=minutes)
