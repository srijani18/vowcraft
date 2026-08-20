import type { RuleContext } from '../types'

/** Shared payload readers for rules. Tolerant of the shapes an LLM actually emits. */

export function readDate(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number' && Number.isFinite(value)) return new Date(value)
  if (typeof value === 'string' && value.trim()) {
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? null : d
  }
  return null
}

export function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  return null
}

export function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** The time window a CALENDAR or REMINDER payload occupies, when determinable. */
export function scheduledWindow(ctx: RuleContext): { start: Date; end: Date } | null {
  const { payload, item } = ctx

  if (item.actionType === 'REMINDER') {
    const at = readDate(payload.remindAt)
    return at ? { start: at, end: at } : null
  }

  const start = readDate(payload.startsAt)
  if (!start) return null
  const minutes = readNumber(payload.durationMinutes) ?? 30
  return { start, end: new Date(start.getTime() + minutes * 60_000) }
}

const WEEKDAY_LABEL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

export function weekdayName(index: number): string {
  return WEEKDAY_LABEL[index] ?? 'that day'
}

/** Formats an instant in the user's zone for a message a human will read. */
export function humanTime(date: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
