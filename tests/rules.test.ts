import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { evaluateRules, RULES } from '@/domain/rules/index'
import type { Rule } from '@/domain/types'
import {
  ctx,
  item,
  FRI_10AM_IST,
  FRI_3AM_IST,
  SAT_11AM_IST,
  THU_5PM_IST,
  validCalendarPayload,
  YESTERDAY,
} from './fixtures'

/** Ids of the violations an evaluation produced, for compact assertions. */
const ids = (result: { violations: { ruleId: string }[] }) => result.violations.map((v) => v.ruleId)

const calendar = (payload: Record<string, unknown>, overrides = {}) =>
  evaluateRules(ctx({ item: item({ actionType: 'CALENDAR', payload }), payload, ...overrides }))

describe('scheduling guardrails — SPEC-003 §4', () => {
  test('a valid weekday slot inside working hours passes cleanly', () => {
    const result = calendar(validCalendarPayload)
    assert.deepEqual(ids(result), [])
    assert.equal(result.passes, true)
  })

  test('SCHED_PAST blocks a start time already gone', () => {
    const result = calendar({ ...validCalendarPayload, startsAt: YESTERDAY.toISOString() })
    assert.ok(ids(result).includes('SCHED_PAST'))
    assert.equal(result.passes, false)
  })

  test('SCHED_WEEKEND blocks Saturday, and names the day', () => {
    const result = calendar({ ...validCalendarPayload, startsAt: SAT_11AM_IST.toISOString() })
    assert.ok(ids(result).includes('SCHED_WEEKEND'))
    assert.match(result.violations.find((v) => v.ruleId === 'SCHED_WEEKEND')!.message, /Saturday/)
  })

  test('SCHED_WEEKEND stands down when weekends are allowed', () => {
    const result = calendar(
      { ...validCalendarPayload, startsAt: SAT_11AM_IST.toISOString() },
      { settings: { ...ctx().settings, allowWeekends: true } },
    )
    assert.ok(!ids(result).includes('SCHED_WEEKEND'))
  })

  test('SCHED_HOURS blocks 03:00 in the user’s own zone, not the server’s', () => {
    const result = calendar({ ...validCalendarPayload, startsAt: FRI_3AM_IST.toISOString() })
    assert.ok(ids(result).includes('SCHED_HOURS'))
    const message = result.violations.find((v) => v.ruleId === 'SCHED_HOURS')!.message
    assert.match(message, /Asia\/Kolkata/)
    assert.match(message, /09:00–18:00/)
  })

  test('SCHED_HOURS blocks a meeting that starts in hours but overruns the day', () => {
    // 17:00 IST + 120 min ends at 19:00, past an 18:00 close.
    const result = calendar({
      ...validCalendarPayload,
      startsAt: THU_5PM_IST.toISOString(),
      durationMinutes: 120,
    })
    assert.ok(ids(result).includes('SCHED_HOURS'))
  })

  test('SCHED_MAX_DURATION blocks an over-long meeting and states the limit', () => {
    const result = calendar({ ...validCalendarPayload, durationMinutes: 180 })
    const violation = result.violations.find((v) => v.ruleId === 'SCHED_MAX_DURATION')!
    assert.match(violation.message, /180 minutes exceeds the 120-minute maximum/)
  })

  test('SCHED_CONFLICT blocks an overlap and names the clashing event', () => {
    const result = calendar(validCalendarPayload, {
      busyBlocks: [
        {
          title: 'Sprint planning',
          startsAt: FRI_10AM_IST,
          endsAt: new Date(FRI_10AM_IST.getTime() + 60 * 60_000),
          kind: 'BUSY' as const,
        },
      ],
    })
    assert.ok(ids(result).includes('SCHED_CONFLICT'))
    assert.match(result.violations.find((v) => v.ruleId === 'SCHED_CONFLICT')!.message, /Sprint planning/)
  })

  test('back-to-back is not a conflict — intervals are half-open', () => {
    const result = calendar(validCalendarPayload, {
      busyBlocks: [
        {
          title: 'Standup',
          startsAt: new Date(FRI_10AM_IST.getTime() - 30 * 60_000),
          endsAt: FRI_10AM_IST,
          kind: 'BUSY' as const,
        },
      ],
    })
    assert.ok(!ids(result).includes('SCHED_CONFLICT'))
  })

  test('SCHED_DND protects a lunch block', () => {
    const result = calendar(validCalendarPayload, {
      busyBlocks: [
        {
          title: 'Lunch',
          startsAt: FRI_10AM_IST,
          endsAt: new Date(FRI_10AM_IST.getTime() + 45 * 60_000),
          kind: 'LUNCH' as const,
        },
      ],
    })
    assert.ok(ids(result).includes('SCHED_DND'))
    // A protected block is DND, not a plain conflict.
    assert.ok(!ids(result).includes('SCHED_CONFLICT'))
  })

  test('SCHED_BUFFER only warns, and reports the actual gap', () => {
    const result = calendar(validCalendarPayload, {
      busyBlocks: [
        {
          title: 'Standup',
          startsAt: new Date(FRI_10AM_IST.getTime() - 35 * 60_000),
          endsAt: new Date(FRI_10AM_IST.getTime() - 5 * 60_000),
          kind: 'BUSY' as const,
        },
      ],
    })
    const violation = result.violations.find((v) => v.ruleId === 'SCHED_BUFFER')!
    assert.equal(violation.severity, 'WARN')
    assert.match(violation.message, /Only 5 min/)
    // A warning must not block.
    assert.equal(result.passes, true)
  })

  test('a comfortable gap produces no buffer warning', () => {
    const result = calendar(validCalendarPayload, {
      busyBlocks: [
        {
          title: 'Standup',
          startsAt: new Date(FRI_10AM_IST.getTime() - 90 * 60_000),
          endsAt: new Date(FRI_10AM_IST.getTime() - 60 * 60_000),
          kind: 'BUSY' as const,
        },
      ],
    })
    assert.ok(!ids(result).includes('SCHED_BUFFER'))
  })

  test('scheduling rules stay silent when there is no time to judge', () => {
    // An incomplete payload is VAL_REQUIRED_FIELDS' problem, not SCHED_HOURS'.
    const result = calendar({ title: 'Budget review', durationMinutes: 30 })
    assert.ok(!ids(result).includes('SCHED_HOURS'))
    assert.ok(!ids(result).includes('SCHED_WEEKEND'))
    assert.ok(ids(result).includes('VAL_REQUIRED_FIELDS'))
  })
})

describe('validation guardrails — SPEC-003 §4', () => {
  test('VAL_REQUIRED_FIELDS names the missing fields by label', () => {
    const result = calendar({ title: 'Budget review' })
    const violation = result.violations.find((v) => v.ruleId === 'VAL_REQUIRED_FIELDS')!
    assert.match(violation.message, /Starts at/)
    assert.match(violation.message, /Attendees/)
    assert.equal(violation.field, 'startsAt')
  })

  test('VAL_EMAIL_FORMAT blocks a malformed attendee', () => {
    const result = calendar({ ...validCalendarPayload, attendees: ['not-an-address'] })
    assert.ok(ids(result).includes('VAL_EMAIL_FORMAT'))
  })

  test('VAL_DEADLINE_PAST blocks a due date in the past', () => {
    const payload = { title: 'Update the deck', dueAt: YESTERDAY.toISOString() }
    const result = evaluateRules(ctx({ item: item({ actionType: 'TASK', payload }), payload }))
    assert.ok(ids(result).includes('VAL_DEADLINE_PAST'))
  })

  test('VAL_OWNER_KNOWN warns for someone off the roster, and does not block', () => {
    const result = calendar(validCalendarPayload, {
      item: item({ actionType: 'CALENDAR', ownerName: 'Stranger', ownerEmail: null, payload: validCalendarPayload }),
    })
    const violation = result.violations.find((v) => v.ruleId === 'VAL_OWNER_KNOWN')!
    assert.equal(violation.severity, 'WARN')
    assert.match(violation.message, /Stranger/)
    assert.equal(result.passes, true)
  })

  test('VAL_OWNER_KNOWN stays quiet when there is no owner at all', () => {
    // Absence is readiness' concern; this rule is about a *wrong* owner.
    const result = calendar(validCalendarPayload, {
      item: item({ actionType: 'CALENDAR', ownerName: null, payload: validCalendarPayload }),
    })
    assert.ok(!ids(result).includes('VAL_OWNER_KNOWN'))
  })

  test('VAL_BUDGET_APPROVAL blocks big money until sign-off is recorded', () => {
    const described = item({
      actionType: 'TASK',
      description: 'Pay the $9,000 renewal invoice',
      payload: { title: 'Pay renewal' },
    })
    const blocked = evaluateRules(ctx({ item: described, payload: described.payload }))
    assert.ok(ids(blocked).includes('VAL_BUDGET_APPROVAL'))

    const approved = { title: 'Pay renewal', managerApproved: true }
    const cleared = evaluateRules(ctx({ item: { ...described, payload: approved }, payload: approved }))
    assert.ok(!ids(cleared).includes('VAL_BUDGET_APPROVAL'))
  })
})

describe('policy guardrails — SPEC-003 §4', () => {
  const email = (payload: Record<string, unknown>, overrides = {}) =>
    evaluateRules(ctx({ item: item({ actionType: 'EMAIL', payload }), payload, ...overrides }))

  test('POL_EXTERNAL_EMAIL blocks an unapproved external send', () => {
    const result = email({
      to: ['accounts@vendor.example'],
      subject: 'Delay',
      body: 'x',
      sendMode: 'send',
    })
    assert.ok(ids(result).includes('POL_EXTERNAL_EMAIL'))
  })

  test('…and stands down once a human has explicitly approved', () => {
    const result = email(
      { to: ['accounts@vendor.example'], subject: 'Delay', body: 'x', sendMode: 'send' },
      { hasExplicitApproval: true },
    )
    assert.ok(!ids(result).includes('POL_EXTERNAL_EMAIL'))
  })

  test('…and never fires for a draft, because nothing is sent', () => {
    const result = email({
      to: ['accounts@vendor.example'],
      subject: 'Delay',
      body: 'x',
      sendMode: 'draft',
    })
    assert.ok(!ids(result).includes('POL_EXTERNAL_EMAIL'))
  })

  test('POL_NO_FINANCIAL_AUTOEXEC blocks a payment without a human decision', () => {
    const payload = { title: 'Pay the invoice' }
    const result = evaluateRules(
      ctx({
        item: item({ actionType: 'TASK', description: 'Pay the vendor invoice', payload }),
        payload,
      }),
    )
    assert.ok(ids(result).includes('POL_NO_FINANCIAL_AUTOEXEC'))
  })

  test('POL_SUPERSEDED blocks an action the conversation replaced', () => {
    const result = calendar(validCalendarPayload, {
      item: item({ actionType: 'CALENDAR', payload: validCalendarPayload, supersededById: 'itm_later' }),
    })
    const violation = result.violations.find((v) => v.ruleId === 'POL_SUPERSEDED')!
    assert.equal(violation.severity, 'BLOCK')
    assert.match(violation.remedy!, /replacement/)
  })

  test('POL_EXPORT_CONSENT blocks an export until consent is recorded', () => {
    const payload = { title: 'Export everything' }
    const described = item({ actionType: 'TASK', description: 'Export the full transcript archive', payload })
    assert.ok(ids(evaluateRules(ctx({ item: described, payload }))).includes('POL_EXPORT_CONSENT'))

    const consented = { ...payload, exportConsent: true }
    assert.ok(
      !ids(evaluateRules(ctx({ item: { ...described, payload: consented }, payload: consented }))).includes(
        'POL_EXPORT_CONSENT',
      ),
    )
  })

  test('POL_CONTRADICTS_DECISION warns when an action negates a recorded decision', () => {
    const payload = { title: 'Skip legal review' }
    const result = evaluateRules(
      ctx({
        item: item({
          actionType: 'TASK',
          description: 'Do not wait for the legal signoff on the vendor contract',
          payload,
        }),
        payload,
        decisions: [
          { statement: 'Hold the vendor contract until legal signoff is complete.', sourceTimestampMs: 305_000 },
        ],
      }),
    )
    const violation = result.violations.find((v) => v.ruleId === 'POL_CONTRADICTS_DECISION')
    assert.ok(violation, 'expected a contradiction warning')
    assert.equal(violation!.severity, 'WARN')
  })

  test('…and stays quiet without a negation, so it is not a blanket warning', () => {
    const payload = { title: 'Follow up with legal' }
    const result = evaluateRules(
      ctx({
        item: item({ actionType: 'TASK', description: 'Follow up with legal on the vendor contract', payload }),
        payload,
        decisions: [{ statement: 'Hold the vendor contract until legal signoff.', sourceTimestampMs: 1 }],
      }),
    )
    assert.ok(!ids(result).includes('POL_CONTRADICTS_DECISION'))
  })
})

describe('the engine itself — SPEC-003 §2', () => {
  test('violations are ordered BLOCK, then WARN, then INFO', () => {
    const result = calendar(
      { ...validCalendarPayload, startsAt: SAT_11AM_IST.toISOString(), durationMinutes: 200 },
      {
        item: item({
          actionType: 'CALENDAR',
          ownerName: 'Stranger',
          ownerEmail: null,
          payload: validCalendarPayload,
        }),
      },
    )
    const severities = result.violations.map((v) => v.severity)
    const rank = { BLOCK: 0, WARN: 1, INFO: 2 } as const
    const sorted = [...severities].sort((a, b) => rank[a] - rank[b])
    assert.deepEqual(severities, sorted)
  })

  test('a rule that throws is contained, and the others still run', () => {
    // A bug in one guardrail must not disable the other sixteen — that failure mode
    // turns a safety net into a hazard.
    const exploding: Rule = {
      id: 'BOOM',
      appliesTo: ['CALENDAR'],
      severity: 'BLOCK',
      evaluate() {
        throw new Error('bad regex')
      },
    }
    const payload = { ...validCalendarPayload, startsAt: SAT_11AM_IST.toISOString() }
    const result = evaluateRules(ctx({ item: item({ actionType: 'CALENDAR', payload }), payload }), [
      exploding,
      ...RULES,
    ])

    const boom = result.violations.find((v) => v.ruleId === 'BOOM')!
    assert.equal(boom.severity, 'INFO', 'a broken rule must degrade to advisory, not block everything')
    assert.match(boom.message, /could not be evaluated/)
    assert.ok(ids(result).includes('SCHED_WEEKEND'), 'the working rules must still fire')
  })

  test('rules only apply to their declared action types', () => {
    const payload = { message: 'Check on legal', remindAt: FRI_10AM_IST.toISOString(), channel: 'self' }
    const result = evaluateRules(ctx({ item: item({ actionType: 'REMINDER', payload }), payload }))
    // Weekend and duration rules are calendar-only.
    assert.ok(!ids(result).includes('SCHED_WEEKEND'))
    assert.ok(!ids(result).includes('SCHED_MAX_DURATION'))
  })

  test('an evaluation with no rules is trivially passing', () => {
    const result = evaluateRules(ctx(), [])
    assert.deepEqual(result.violations, [])
    assert.equal(result.passes, true)
  })

  test('passes reflects blocking violations only', () => {
    const warnOnly = calendar(validCalendarPayload, {
      busyBlocks: [
        {
          title: 'Standup',
          startsAt: new Date(FRI_10AM_IST.getTime() - 35 * 60_000),
          endsAt: new Date(FRI_10AM_IST.getTime() - 5 * 60_000),
          kind: 'BUSY' as const,
        },
      ],
    })
    assert.equal(warnOnly.warnings.length, 1)
    assert.equal(warnOnly.blocking.length, 0)
    assert.equal(warnOnly.passes, true)
  })
})
