/**
 * Time helpers. Everything here takes `now` as an argument rather than reading
 * the clock, because the rule engine that consumes them must stay pure and
 * testable (SPEC-003 §2) — "no meetings on a Sunday" should be a test, not a hope.
 */

export const MINUTE = 60_000
export const HOUR = 60 * MINUTE
export const DAY = 24 * HOUR

/** Parses "HH:mm" into minutes past midnight. Returns null when malformed. */
export function parseClock(value: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(value.trim())
  if (!m) return null
  const h = Number(m[1])
  const min = Number(m[2])
  if (h > 23 || min > 59) return null
  return h * 60 + min
}

/**
 * Wall-clock parts of `date` in `timeZone`, via Intl rather than a date library.
 * `Intl` ships with the runtime, is DST-correct, and needs no dependency —
 * which matters because a scheduling guardrail that is wrong twice a year is
 * worse than no guardrail.
 */
export function zonedParts(
  date: Date,
  timeZone: string,
): { year: number; month: number; day: number; hour: number; minute: number; weekday: number } {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    weekday: 'short',
  })
  const parts = Object.fromEntries(fmt.formatToParts(date).map((p) => [p.type, p.value]))
  const weekdayMap: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    // Intl renders midnight as "24" in some locales/engines; normalise it.
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
    weekday: weekdayMap[parts.weekday ?? 'Mon'] ?? 1,
  }
}

export function minutesIntoDay(date: Date, timeZone: string): number {
  const { hour, minute } = zonedParts(date, timeZone)
  return hour * 60 + minute
}

export function isWeekend(date: Date, timeZone: string): boolean {
  const { weekday } = zonedParts(date, timeZone)
  return weekday === 0 || weekday === 6
}

export function overlaps(aStart: Date, aEnd: Date, bStart: Date, bEnd: Date): boolean {
  // Half-open intervals: a meeting ending exactly when the next begins is fine.
  return aStart < bEnd && bStart < aEnd
}

export function gapMinutes(aEnd: Date, bStart: Date): number {
  return Math.round((bStart.getTime() - aEnd.getTime()) / MINUTE)
}

/** "12:34" / "1:02:03" from a millisecond offset into a recording. */
export function formatTimestamp(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || ms < 0) return null
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

export type DeadlineBucket = 'overdue' | 'today' | 'week' | 'none'

export function deadlineBucket(deadline: Date | null, now: Date): DeadlineBucket {
  if (!deadline) return 'none'
  const diff = deadline.getTime() - now.getTime()
  if (diff < 0) return 'overdue'
  if (diff <= DAY) return 'today'
  if (diff <= 7 * DAY) return 'week'
  return 'none'
}

/** Compact relative phrasing for the card ("in 3d", "2h overdue"). */
export function relativeLabel(target: Date, now: Date): string {
  const diff = target.getTime() - now.getTime()
  const past = diff < 0
  const abs = Math.abs(diff)
  const unit =
    abs < HOUR ? `${Math.max(1, Math.round(abs / MINUTE))}m`
    : abs < DAY ? `${Math.round(abs / HOUR)}h`
    : `${Math.round(abs / DAY)}d`
  return past ? `${unit} overdue` : `in ${unit}`
}
