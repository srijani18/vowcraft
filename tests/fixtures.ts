import type { ActionItemCore, RuleContext, SettingsView } from '@/domain/types'

/**
 * Fixtures for the pure suite.
 *
 * `NOW` is a fixed instant — a Thursday, 09:00 UTC, which is 14:30 in Asia/Kolkata
 * and therefore mid-working-day for the default settings. Every time-dependent
 * assertion is relative to it, so no test can pass or fail because of when it ran.
 */
export const NOW = new Date('2026-08-20T09:00:00.000Z') // Thursday

/** Offsets from NOW, in the default Asia/Kolkata zone, for readability. */
export const at = (iso: string) => new Date(iso)

/*
 * All of these are checked against NOW (Thu 14:30 IST) as well as against the
 * working-hours window, so a slot meant to be "valid" is valid on both counts.
 * An earlier version used Thu 11:00 IST, which is inside working hours but 3.5
 * hours *before* NOW — SCHED_PAST caught it, correctly.
 */
export const FRI_10AM_IST = at('2026-08-21T04:30:00.000Z') // Fri 10:00 IST — future, in hours
export const THU_5PM_IST = at('2026-08-20T11:30:00.000Z') // Thu 17:00 IST — future, in hours
export const FRI_3AM_IST = at('2026-08-20T21:30:00.000Z') // Fri 03:00 IST — future, OUT of hours
export const SAT_11AM_IST = at('2026-08-22T05:30:00.000Z') // Sat 11:00 IST — future, weekend
export const YESTERDAY = at('2026-08-19T05:30:00.000Z') // past

export const settings: SettingsView = {
  timeZone: 'Asia/Kolkata',
  workdayStart: '09:00',
  workdayEnd: '18:00',
  allowWeekends: false,
  maxMeetingMinutes: 120,
  minBufferMinutes: 15,
  orgDomains: ['acme.test'],
  autoExecuteLowRisk: false,
  budgetApprovalLimit: 1000,
  orgCurrency: 'USD',
  approvalThresholds: {},
}

export function item(overrides: Partial<ActionItemCore> = {}): ActionItemCore {
  return {
    id: 'itm_1',
    description: 'Schedule the budget review',
    actionType: 'CALENDAR',
    status: 'PROPOSED',
    priority: 'MEDIUM',
    confidence: 'HIGH',
    ownerName: 'Marcus',
    ownerEmail: 'marcus@acme.test',
    deadline: null,
    sourceTimestampMs: 61_000,
    sourceQuote: null,
    payload: {},
    supersededById: null,
    dependsOnId: null,
    ...overrides,
  }
}

export function ctx(overrides: Partial<RuleContext> = {}): RuleContext {
  const core = overrides.item ?? item()
  return {
    item: core,
    payload: overrides.payload ?? core.payload,
    now: NOW,
    settings,
    busyBlocks: [],
    teamMembers: [
      { name: 'Marcus', email: 'marcus@acme.test' },
      { name: 'Priya', email: 'priya@acme.test' },
    ],
    decisions: [],
    hasExplicitApproval: false,
    ...overrides,
  }
}

/** A complete, valid calendar payload inside working hours on a weekday. */
export const validCalendarPayload = {
  title: 'Budget review',
  startsAt: FRI_10AM_IST.toISOString(),
  durationMinutes: 45,
  attendees: ['priya@acme.test'],
  timeZone: 'Asia/Kolkata',
}
