import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import {
  DAY,
  deadlineBucket,
  formatTimestamp,
  gapMinutes,
  isWeekend,
  minutesIntoDay,
  overlaps,
  parseClock,
  relativeLabel,
  zonedParts,
} from '@/lib/time'

const NOW = new Date('2026-08-20T09:00:00.000Z') // Thursday, 14:30 IST

describe('parseClock', () => {
  test('accepts valid times, with or without a leading zero', () => {
    assert.equal(parseClock('09:00'), 540)
    assert.equal(parseClock('9:00'), 540)
    assert.equal(parseClock('00:00'), 0)
    assert.equal(parseClock('23:59'), 1_439)
    assert.equal(parseClock(' 18:30 '), 1_110)
  })

  test('rejects anything a scheduling rule must not silently accept', () => {
    // A malformed clock makes SCHED_HOURS return "no opinion", so this matters.
    for (const bad of ['24:00', '12:60', '99:99', '9', '9:5', 'noon', '', '9:00pm']) {
      assert.equal(parseClock(bad), null, `expected null for ${JSON.stringify(bad)}`)
    }
  })
})

describe('zonedParts', () => {
  test('reads wall-clock parts in the requested zone, not the server’s', () => {
    const parts = zonedParts(NOW, 'Asia/Kolkata')
    assert.equal(parts.hour, 14)
    assert.equal(parts.minute, 30)
    assert.equal(parts.weekday, 4) // Thursday
    assert.equal(parts.day, 20)
    assert.equal(parts.month, 8)
  })

  test('the same instant is a different day in different zones', () => {
    const late = new Date('2026-08-20T20:00:00.000Z')
    assert.equal(zonedParts(late, 'UTC').day, 20)
    assert.equal(zonedParts(late, 'Asia/Kolkata').day, 21) // 01:30 next day
  })

  test('midnight is hour 0, never 24', () => {
    // Some engines format midnight as "24"; the helper normalises it.
    const midnightIst = new Date('2026-08-19T18:30:00.000Z')
    assert.equal(zonedParts(midnightIst, 'Asia/Kolkata').hour, 0)
  })

  test('DST is handled, because Intl handles it', () => {
    // New York is UTC-4 in August and UTC-5 in January.
    const summer = new Date('2026-08-20T16:00:00.000Z')
    const winter = new Date('2026-01-20T16:00:00.000Z')
    assert.equal(zonedParts(summer, 'America/New_York').hour, 12)
    assert.equal(zonedParts(winter, 'America/New_York').hour, 11)
  })
})

describe('minutesIntoDay and isWeekend', () => {
  test('minutesIntoDay is zone-aware', () => {
    assert.equal(minutesIntoDay(NOW, 'Asia/Kolkata'), 14 * 60 + 30)
    assert.equal(minutesIntoDay(NOW, 'UTC'), 9 * 60)
  })

  test('isWeekend is zone-aware at the boundary', () => {
    // Fri 23:00 UTC is already Saturday in Kolkata.
    const fridayLate = new Date('2026-08-21T19:00:00.000Z')
    assert.equal(isWeekend(fridayLate, 'UTC'), false)
    assert.equal(isWeekend(fridayLate, 'Asia/Kolkata'), true)
  })

  test('Saturday and Sunday are both weekend', () => {
    assert.ok(isWeekend(new Date('2026-08-22T06:00:00Z'), 'UTC'))
    assert.ok(isWeekend(new Date('2026-08-23T06:00:00Z'), 'UTC'))
    assert.ok(!isWeekend(new Date('2026-08-24T06:00:00Z'), 'UTC'))
  })
})

describe('overlaps — half-open intervals', () => {
  const a = new Date('2026-08-20T10:00:00Z')
  const b = new Date('2026-08-20T11:00:00Z')

  test('a genuine overlap is detected in both directions', () => {
    const mid = new Date('2026-08-20T10:30:00Z')
    const later = new Date('2026-08-20T11:30:00Z')
    assert.ok(overlaps(a, b, mid, later))
    assert.ok(overlaps(mid, later, a, b))
  })

  test('touching endpoints do not overlap — back-to-back is fine', () => {
    const c = new Date('2026-08-20T12:00:00Z')
    assert.ok(!overlaps(a, b, b, c))
    assert.ok(!overlaps(b, c, a, b))
  })

  test('containment counts as overlap', () => {
    const inner1 = new Date('2026-08-20T10:15:00Z')
    const inner2 = new Date('2026-08-20T10:45:00Z')
    assert.ok(overlaps(a, b, inner1, inner2))
    assert.ok(overlaps(inner1, inner2, a, b))
  })

  test('a zero-length instant inside a window does coincide with it', () => {
    /*
     * A point in time inside a busy block genuinely coincides with it — a reminder
     * firing mid-meeting is during the meeting. Only the *endpoints* are exclusive.
     *
     * In practice nothing exercises this: SCHED_CONFLICT, SCHED_DND, and
     * SCHED_BUFFER all declare `appliesTo: ['CALENDAR']`, and only REMINDER
     * produces a zero-length window. Asserted anyway so the semantics are pinned
     * before some future rule starts relying on them.
     */
    const instant = new Date('2026-08-20T10:30:00Z')
    assert.ok(overlaps(instant, instant, a, b))
    // But an instant exactly on a boundary does not.
    assert.ok(!overlaps(a, a, a, b))
    assert.ok(!overlaps(b, b, a, b))
  })
})

describe('gapMinutes', () => {
  test('returns a positive gap between two events', () => {
    assert.equal(gapMinutes(new Date('2026-08-20T10:00:00Z'), new Date('2026-08-20T10:20:00Z')), 20)
  })

  test('returns negative when they are the wrong way round', () => {
    assert.ok(gapMinutes(new Date('2026-08-20T10:20:00Z'), new Date('2026-08-20T10:00:00Z')) < 0)
  })
})

describe('formatTimestamp', () => {
  test('formats under an hour as m:ss', () => {
    assert.equal(formatTimestamp(0), '0:00')
    assert.equal(formatTimestamp(61_000), '1:01')
    assert.equal(formatTimestamp(754_000), '12:34')
    assert.equal(formatTimestamp(3_599_000), '59:59')
  })

  test('formats an hour or more as h:mm:ss', () => {
    assert.equal(formatTimestamp(3_600_000), '1:00:00')
    assert.equal(formatTimestamp(3_723_000), '1:02:03')
  })

  test('returns null for absent or nonsensical input', () => {
    assert.equal(formatTimestamp(null), null)
    assert.equal(formatTimestamp(undefined), null)
    assert.equal(formatTimestamp(-1), null)
  })
})

describe('deadlineBucket', () => {
  test('classifies relative to the instant passed in', () => {
    assert.equal(deadlineBucket(null, NOW), 'none')
    assert.equal(deadlineBucket(new Date(NOW.getTime() - 1_000), NOW), 'overdue')
    assert.equal(deadlineBucket(new Date(NOW.getTime() + 6 * 3_600_000), NOW), 'today')
    assert.equal(deadlineBucket(new Date(NOW.getTime() + 3 * DAY), NOW), 'week')
    assert.equal(deadlineBucket(new Date(NOW.getTime() + 30 * DAY), NOW), 'none')
  })

  test('the boundaries are inclusive on the near side', () => {
    assert.equal(deadlineBucket(new Date(NOW.getTime() + DAY), NOW), 'today')
    assert.equal(deadlineBucket(new Date(NOW.getTime() + DAY + 1), NOW), 'week')
    assert.equal(deadlineBucket(new Date(NOW.getTime() + 7 * DAY), NOW), 'week')
  })
})

describe('relativeLabel', () => {
  test('reads forwards and backwards', () => {
    assert.equal(relativeLabel(new Date(NOW.getTime() + 3 * DAY), NOW), 'in 3d')
    assert.equal(relativeLabel(new Date(NOW.getTime() - 2 * 3_600_000), NOW), '2h overdue')
    assert.equal(relativeLabel(new Date(NOW.getTime() + 30 * 60_000), NOW), 'in 30m')
  })

  test('never rounds a near-future instant down to zero', () => {
    assert.equal(relativeLabel(new Date(NOW.getTime() + 5_000), NOW), 'in 1m')
  })
})
