"""Scheduling guardrails — SPEC-003 §3. Seven rules about *when* something happens."""

from __future__ import annotations

from typing import Optional

from app.domain.timeutil import (
    gap_minutes,
    human_time,
    is_weekend,
    minutes_into_day,
    overlaps,
    parse_clock,
    read_number,
    scheduled_window,
    weekday_name,
    zoned_parts,
)
from app.domain.types import Rule, RuleContext, RuleViolation


def _window(ctx: RuleContext):
    return scheduled_window(ctx.item.action_type, ctx.payload)


def _sched_past(ctx: RuleContext) -> Optional[RuleViolation]:
    window = _window(ctx)
    if not window:
        return None
    start, _ = window
    if start > ctx.now:
        return None
    return RuleViolation(
        rule_id="SCHED_PAST",
        severity="BLOCK",
        message=(
            f"Scheduled for {human_time(start, ctx.settings.time_zone)}, which is in the past."
        ),
        remedy="Pick a future time.",
    )


def _sched_weekend(ctx: RuleContext) -> Optional[RuleViolation]:
    if ctx.settings.allow_weekends:
        return None
    window = _window(ctx)
    if not window:
        return None
    start, _ = window
    if not is_weekend(start, ctx.settings.time_zone):
        return None
    weekday = zoned_parts(start, ctx.settings.time_zone).weekday
    return RuleViolation(
        rule_id="SCHED_WEEKEND",
        severity="BLOCK",
        message=(
            f"Falls on {weekday_name(weekday)}. Weekend meetings are disabled for this workspace."
        ),
        remedy="Move it to a weekday, or enable weekends in settings.",
    )


def _sched_hours(ctx: RuleContext) -> Optional[RuleViolation]:
    window = _window(ctx)
    if not window:
        return None
    start, end = window
    tz = ctx.settings.time_zone
    open_min = parse_clock(ctx.settings.workday_start)
    close_min = parse_clock(ctx.settings.workday_end)
    # Malformed settings: no opinion. Blocking on a value the user cannot see is worse
    # than staying quiet.
    if open_min is None or close_min is None:
        return None

    start_min = minutes_into_day(start, tz)
    end_min = minutes_into_day(end, tz)
    wraps = end_min < start_min
    if not wraps and start_min >= open_min and end_min <= close_min:
        return None

    return RuleViolation(
        rule_id="SCHED_HOURS",
        severity="BLOCK",
        message=(
            f"Runs {human_time(start, tz)}–{human_time(end, tz)}, outside working hours "
            f"({ctx.settings.workday_start}–{ctx.settings.workday_end} {tz})."
        ),
        remedy=(
            f"Move it inside {ctx.settings.workday_start}–{ctx.settings.workday_end}, or widen "
            "your working hours."
        ),
    )


def _sched_max_duration(ctx: RuleContext) -> Optional[RuleViolation]:
    minutes = read_number(ctx.payload.get("durationMinutes"))
    if minutes is None:
        return None
    maximum = ctx.settings.max_meeting_minutes
    if minutes <= maximum:
        return None
    shown = int(minutes) if minutes == int(minutes) else minutes
    return RuleViolation(
        rule_id="SCHED_MAX_DURATION",
        severity="BLOCK",
        message=f"{shown} minutes exceeds the {maximum}-minute maximum for a single meeting.",
        remedy=f"Shorten it to {maximum} minutes or less, or split it into sessions.",
    )


def _sched_conflict(ctx: RuleContext) -> Optional[RuleViolation]:
    window = _window(ctx)
    if not window:
        return None
    start, end = window
    # FOCUS and LUNCH are handled by SCHED_DND, which says something more specific about
    # them. Reporting both for one overlap would be two messages for one problem.
    clash = next(
        (
            b
            for b in ctx.busy_blocks
            if b.kind not in ("FOCUS", "LUNCH")
            and overlaps(start, end, b.starts_at, b.ends_at)
        ),
        None,
    )
    if not clash:
        return None
    return RuleViolation(
        rule_id="SCHED_CONFLICT",
        severity="BLOCK",
        message=(
            f"Overlaps “{clash.title or 'an existing event'}” "
            f"({human_time(clash.starts_at, ctx.settings.time_zone)})."
        ),
        remedy="Choose a free slot, or move the existing event first.",
    )


def _sched_dnd(ctx: RuleContext) -> Optional[RuleViolation]:
    window = _window(ctx)
    if not window:
        return None
    start, end = window
    blocked = next(
        (
            b
            for b in ctx.busy_blocks
            if b.kind in ("FOCUS", "LUNCH", "OOO")
            and overlaps(start, end, b.starts_at, b.ends_at)
        ),
        None,
    )
    if not blocked:
        return None
    return RuleViolation(
        rule_id="SCHED_DND",
        severity="BLOCK",
        message=f"Lands inside a protected block: {blocked.title or blocked.kind.lower()}.",
        remedy="Pick a time outside your protected blocks.",
    )


def _sched_buffer(ctx: RuleContext) -> Optional[RuleViolation]:
    window = _window(ctx)
    if not window:
        return None
    start, end = window
    minimum = ctx.settings.min_buffer_minutes
    tightest: Optional[tuple[float, str]] = None

    for block in ctx.busy_blocks:
        # An overlap is SCHED_CONFLICT's business; this rule is about the gap between
        # things that do *not* overlap.
        if overlaps(start, end, block.starts_at, block.ends_at):
            continue
        for gap in (gap_minutes(block.ends_at, start), gap_minutes(end, block.starts_at)):
            if gap < 0 or gap >= minimum:
                continue
            if tightest is None or gap < tightest[0]:
                tightest = (gap, block.title or "another event")

    if tightest is None:
        return None
    gap, title = tightest
    shown = int(gap) if gap == int(gap) else round(gap, 1)
    return RuleViolation(
        rule_id="SCHED_BUFFER",
        # WARN, not BLOCK: a tight gap is a bad idea, not an impossibility, and the person
        # scheduling may know something the calendar does not.
        severity="WARN",
        message=f"Only {shown} min between this and “{title}” ({minimum} min preferred).",
        remedy="Shift it slightly to leave room to move between meetings.",
    )


SCHED_PAST = Rule("SCHED_PAST", "Not in the past", "BLOCK", ("CALENDAR", "REMINDER"), _sched_past)
SCHED_WEEKEND = Rule("SCHED_WEEKEND", "No weekend meetings", "BLOCK", ("CALENDAR",), _sched_weekend)
SCHED_HOURS = Rule("SCHED_HOURS", "Inside working hours", "BLOCK", ("CALENDAR",), _sched_hours)
SCHED_MAX_DURATION = Rule(
    "SCHED_MAX_DURATION", "Under the duration cap", "BLOCK", ("CALENDAR",), _sched_max_duration
)
SCHED_CONFLICT = Rule("SCHED_CONFLICT", "No double booking", "BLOCK", ("CALENDAR",), _sched_conflict)
SCHED_DND = Rule("SCHED_DND", "Respect protected time", "BLOCK", ("CALENDAR",), _sched_dnd)
SCHED_BUFFER = Rule("SCHED_BUFFER", "Leave a buffer", "WARN", ("CALENDAR",), _sched_buffer)

SCHEDULING_RULES: tuple[Rule, ...] = (
    SCHED_PAST,
    SCHED_WEEKEND,
    SCHED_HOURS,
    SCHED_MAX_DURATION,
    SCHED_CONFLICT,
    SCHED_DND,
    SCHED_BUFFER,
)
