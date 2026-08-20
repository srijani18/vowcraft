import { isWeekend, minutesIntoDay, overlaps, gapMinutes, parseClock, zonedParts } from '@/lib/time'
import type { Rule } from '../types'
import { humanTime, readNumber, scheduledWindow, weekdayName } from './helpers'

/**
 * Scheduling guardrails — SPEC-003 §4.
 *
 * Every rule reads `ctx.now` and `ctx.settings.timeZone` rather than the ambient
 * clock or the server's zone. A "no meetings outside business hours" rule that
 * silently means UTC is worse than no rule at all.
 */

export const SCHED_PAST: Rule = {
  id: 'SCHED_PAST',
  appliesTo: ['CALENDAR', 'REMINDER'],
  severity: 'BLOCK',
  evaluate(ctx) {
    const window = scheduledWindow(ctx)
    if (!window) return null
    if (window.start.getTime() > ctx.now.getTime()) return null
    return {
      ruleId: 'SCHED_PAST',
      severity: 'BLOCK',
      field: ctx.item.actionType === 'REMINDER' ? 'remindAt' : 'startsAt',
      message: `Scheduled for ${humanTime(window.start, ctx.settings.timeZone)}, which is in the past.`,
      remedy: 'Pick a future time.',
    }
  },
}

export const SCHED_WEEKEND: Rule = {
  id: 'SCHED_WEEKEND',
  appliesTo: ['CALENDAR'],
  severity: 'BLOCK',
  evaluate(ctx) {
    if (ctx.settings.allowWeekends) return null
    const window = scheduledWindow(ctx)
    if (!window) return null
    if (!isWeekend(window.start, ctx.settings.timeZone)) return null
    const { weekday } = zonedParts(window.start, ctx.settings.timeZone)
    return {
      ruleId: 'SCHED_WEEKEND',
      severity: 'BLOCK',
      field: 'startsAt',
      message: `Falls on ${weekdayName(weekday)}. Weekend meetings are disabled for this workspace.`,
      remedy: 'Move it to a weekday, or enable weekends in settings.',
    }
  },
}

export const SCHED_HOURS: Rule = {
  id: 'SCHED_HOURS',
  appliesTo: ['CALENDAR'],
  severity: 'BLOCK',
  evaluate(ctx) {
    const window = scheduledWindow(ctx)
    if (!window) return null
    const { timeZone, workdayStart, workdayEnd } = ctx.settings
    const open = parseClock(workdayStart)
    const close = parseClock(workdayEnd)
    if (open === null || close === null) return null // malformed settings: no opinion

    const startMin = minutesIntoDay(window.start, timeZone)
    const endMin = minutesIntoDay(window.end, timeZone)
    // An event that crosses midnight has endMin < startMin; that is out of hours
    // by definition, so treat the wrap as a violation rather than reasoning about it.
    const wraps = endMin < startMin
    if (!wraps && startMin >= open && endMin <= close) return null

    return {
      ruleId: 'SCHED_HOURS',
      severity: 'BLOCK',
      field: 'startsAt',
      message:
        `Runs ${humanTime(window.start, timeZone)}–${humanTime(window.end, timeZone)}, outside ` +
        `working hours (${workdayStart}–${workdayEnd} ${timeZone}).`,
      remedy: `Move it inside ${workdayStart}–${workdayEnd}, or widen your working hours.`,
    }
  },
}

export const SCHED_MAX_DURATION: Rule = {
  id: 'SCHED_MAX_DURATION',
  appliesTo: ['CALENDAR'],
  severity: 'BLOCK',
  evaluate(ctx) {
    const minutes = readNumber(ctx.payload.durationMinutes)
    if (minutes === null) return null
    const max = ctx.settings.maxMeetingMinutes
    if (minutes <= max) return null
    return {
      ruleId: 'SCHED_MAX_DURATION',
      severity: 'BLOCK',
      field: 'durationMinutes',
      message: `${minutes} minutes exceeds the ${max}-minute maximum for a single meeting.`,
      remedy: `Shorten it to ${max} minutes or less, or split it into sessions.`,
    }
  },
}

export const SCHED_CONFLICT: Rule = {
  id: 'SCHED_CONFLICT',
  appliesTo: ['CALENDAR'],
  severity: 'BLOCK',
  evaluate(ctx) {
    const window = scheduledWindow(ctx)
    if (!window) return null
    const clash = ctx.busyBlocks.find(
      (b) => b.kind !== 'FOCUS' && b.kind !== 'LUNCH' && overlaps(window.start, window.end, b.startsAt, b.endsAt),
    )
    if (!clash) return null
    return {
      ruleId: 'SCHED_CONFLICT',
      severity: 'BLOCK',
      field: 'startsAt',
      message:
        `Overlaps “${clash.title ?? 'an existing event'}” ` +
        `(${humanTime(clash.startsAt, ctx.settings.timeZone)}).`,
      remedy: 'Choose a free slot, or move the existing event first.',
    }
  },
}

export const SCHED_DND: Rule = {
  id: 'SCHED_DND',
  appliesTo: ['CALENDAR'],
  severity: 'BLOCK',
  evaluate(ctx) {
    const window = scheduledWindow(ctx)
    if (!window) return null
    const blocked = ctx.busyBlocks.find(
      (b) => (b.kind === 'FOCUS' || b.kind === 'LUNCH' || b.kind === 'OOO') &&
        overlaps(window.start, window.end, b.startsAt, b.endsAt),
    )
    if (!blocked) return null
    return {
      ruleId: 'SCHED_DND',
      severity: 'BLOCK',
      field: 'startsAt',
      message: `Lands inside a protected block: ${blocked.title ?? blocked.kind.toLowerCase()}.`,
      remedy: 'Pick a time outside your protected blocks.',
    }
  },
}

export const SCHED_BUFFER: Rule = {
  id: 'SCHED_BUFFER',
  appliesTo: ['CALENDAR'],
  severity: 'WARN',
  evaluate(ctx) {
    const window = scheduledWindow(ctx)
    if (!window) return null
    const min = ctx.settings.minBufferMinutes
    // Only non-overlapping neighbours matter here; an actual overlap is SCHED_CONFLICT's job.
    let tightest: { gap: number; title: string } | null = null

    for (const block of ctx.busyBlocks) {
      if (overlaps(window.start, window.end, block.startsAt, block.endsAt)) continue
      const before = gapMinutes(block.endsAt, window.start)
      const after = gapMinutes(window.end, block.startsAt)
      for (const gap of [before, after]) {
        if (gap < 0 || gap >= min) continue
        if (!tightest || gap < tightest.gap) {
          tightest = { gap, title: block.title ?? 'another event' }
        }
      }
    }

    if (!tightest) return null
    return {
      ruleId: 'SCHED_BUFFER',
      severity: 'WARN',
      field: 'startsAt',
      message: `Only ${tightest.gap} min between this and “${tightest.title}” (${min} min preferred).`,
      remedy: 'Shift it slightly to leave room to move between meetings.',
    }
  },
}

export const SCHEDULING_RULES: readonly Rule[] = [
  SCHED_PAST,
  SCHED_WEEKEND,
  SCHED_HOURS,
  SCHED_MAX_DURATION,
  SCHED_CONFLICT,
  SCHED_DND,
  SCHED_BUFFER,
]
