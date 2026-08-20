import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import {
  AUDIT_EVENT_FOR_STATUS,
  canTransition,
  compareForReview,
  computeReadiness,
  groupByReadiness,
  isInExecutionLane,
  SERVER_ONLY_STATUSES,
  transitionError,
} from '@/domain/action-item'
import { item, validCalendarPayload } from './fixtures'

describe('computeReadiness — SPEC-001 §6', () => {
  test('a complete, owned, unviolated action is READY', () => {
    const result = computeReadiness(item({ payload: validCalendarPayload }))
    assert.equal(result.readiness, 'READY')
    assert.deepEqual(result.missingFields, [])
    assert.deepEqual(result.reasons, [])
  })

  test('missing required payload fields are named, not just counted', () => {
    const result = computeReadiness(item({ payload: { title: 'Budget review' } }))
    assert.equal(result.readiness, 'NEEDS_CLARIFICATION')
    assert.deepEqual(result.missingFields, ['startsAt', 'durationMinutes', 'attendees'])
    assert.match(result.reasons[0]!, /startsAt, durationMinutes, attendees/)
  })

  test('LOW confidence alone is enough to need clarification', () => {
    const result = computeReadiness(item({ payload: validCalendarPayload, confidence: 'LOW' }))
    assert.equal(result.readiness, 'NEEDS_CLARIFICATION')
    assert.ok(result.reasons.some((r) => /low confidence/i.test(r)))
  })

  test('an unowned action needs clarification even when otherwise complete', () => {
    const result = computeReadiness(item({ payload: validCalendarPayload, ownerName: null }))
    assert.equal(result.readiness, 'NEEDS_CLARIFICATION')
    assert.ok(result.reasons.some((r) => /no owner/i.test(r)))
  })

  test('a blocking violation forces NEEDS_CLARIFICATION and surfaces its message', () => {
    const result = computeReadiness(item({ payload: validCalendarPayload }), [
      { ruleId: 'SCHED_WEEKEND', severity: 'BLOCK', message: 'Falls on Saturday.' },
    ])
    assert.equal(result.readiness, 'NEEDS_CLARIFICATION')
    assert.ok(result.reasons.includes('Falls on Saturday.'))
  })

  test('a WARN does not stop an item being READY', () => {
    const result = computeReadiness(item({ payload: validCalendarPayload }), [
      { ruleId: 'SCHED_BUFFER', severity: 'WARN', message: 'Only 5 min of buffer.' },
    ])
    assert.equal(result.readiness, 'READY')
  })

  test('actionType NONE is INFORMATIONAL regardless of anything else', () => {
    const result = computeReadiness(item({ actionType: 'NONE', confidence: 'LOW', ownerName: null }))
    assert.equal(result.readiness, 'INFORMATIONAL')
  })

  test('a rejected item is INFORMATIONAL even when complete', () => {
    const result = computeReadiness(item({ payload: validCalendarPayload, status: 'REJECTED' }))
    assert.equal(result.readiness, 'INFORMATIONAL')
  })

  test('INFORMATIONAL wins over missing fields — ordering matters', () => {
    // Telling a reviewer to "fix" something that will never execute is noise.
    const result = computeReadiness(item({ actionType: 'NONE', payload: {} }))
    assert.equal(result.readiness, 'INFORMATIONAL')
    assert.deepEqual(result.missingFields, [])
  })
})

describe('status state machine — SPEC-001 §5', () => {
  test('the documented transitions are allowed', () => {
    assert.ok(canTransition('PROPOSED', 'APPROVED'))
    assert.ok(canTransition('PROPOSED', 'REJECTED'))
    assert.ok(canTransition('PROPOSED', 'DEFERRED'))
    assert.ok(canTransition('DEFERRED', 'APPROVED'))
    assert.ok(canTransition('APPROVED', 'EXECUTING'))
    assert.ok(canTransition('EXECUTING', 'EXECUTED'))
    assert.ok(canTransition('EXECUTING', 'FAILED'))
    assert.ok(canTransition('FAILED', 'EXECUTING'))
  })

  test('undocumented jumps are refused', () => {
    assert.ok(!canTransition('PROPOSED', 'EXECUTING'))
    assert.ok(!canTransition('PROPOSED', 'EXECUTED'))
    assert.ok(!canTransition('REJECTED', 'EXECUTED'))
    assert.ok(!canTransition('DEFERRED', 'EXECUTING'))
  })

  test('EXECUTED is terminal, and the message says why', () => {
    assert.deepEqual(canTransition('EXECUTED', 'REJECTED'), false)
    const error = transitionError('EXECUTED', 'REJECTED')
    assert.match(error!, /already been executed/)
    assert.match(error!, /compensating action/)
  })

  test('a no-op transition is not an error', () => {
    assert.equal(transitionError('APPROVED', 'APPROVED'), null)
  })

  test('EXECUTING and EXECUTED are server-assigned only', () => {
    assert.deepEqual([...SERVER_ONLY_STATUSES], ['EXECUTING', 'EXECUTED'])
  })

  test('each human decision maps to exactly one audit event', () => {
    assert.equal(AUDIT_EVENT_FOR_STATUS.APPROVED, 'action_item.approved')
    assert.equal(AUDIT_EVENT_FOR_STATUS.REJECTED, 'action_item.rejected')
    assert.equal(AUDIT_EVENT_FOR_STATUS.DEFERRED, 'action_item.deferred')
    assert.equal(AUDIT_EVENT_FOR_STATUS.PROPOSED, 'action_item.reopened')
    // Server-assigned statuses have their own events, not these.
    assert.equal(AUDIT_EVENT_FOR_STATUS.EXECUTED, undefined)
  })

  test('in-flight and finished items live in the execution lane', () => {
    assert.ok(isInExecutionLane('EXECUTING'))
    assert.ok(isInExecutionLane('EXECUTED'))
    assert.ok(isInExecutionLane('FAILED'))
    assert.ok(!isInExecutionLane('APPROVED'))
    assert.ok(!isInExecutionLane('PROPOSED'))
  })
})

describe('review ordering — SPEC-001 §8', () => {
  test('priority dominates', () => {
    const high = item({ priority: 'HIGH' })
    const low = item({ priority: 'LOW' })
    assert.ok(compareForReview(high, low) < 0)
    assert.ok(compareForReview(low, high) > 0)
  })

  test('within a priority, the nearer deadline comes first', () => {
    const soon = item({ deadline: new Date('2026-08-21T00:00:00Z') })
    const later = item({ deadline: new Date('2026-08-30T00:00:00Z') })
    assert.ok(compareForReview(soon, later) < 0)
  })

  test('items with no deadline sort last, not first', () => {
    // A null deadline must not read as "the epoch" and jump the queue.
    const dated = item({ deadline: new Date('2026-12-31T00:00:00Z') })
    const undated = item({ deadline: null })
    assert.ok(compareForReview(dated, undated) < 0)
  })

  test('source timestamp breaks a remaining tie, so the order is total', () => {
    const early = item({ sourceTimestampMs: 1_000 })
    const late = item({ sourceTimestampMs: 900_000 })
    assert.ok(compareForReview(early, late) < 0)
    assert.equal(compareForReview(early, early), 0)
  })

  test('sorting is stable across repeated runs', () => {
    const items = [
      item({ id: 'a', priority: 'LOW', sourceTimestampMs: 3 }),
      item({ id: 'b', priority: 'HIGH', sourceTimestampMs: 2 }),
      item({ id: 'c', priority: 'MEDIUM', sourceTimestampMs: 1 }),
    ]
    const first = [...items].sort(compareForReview).map((i) => i.id)
    const second = [...items].sort(compareForReview).map((i) => i.id)
    assert.deepEqual(first, ['b', 'c', 'a'])
    assert.deepEqual(first, second)
  })
})

test('groupByReadiness always returns all three lanes, even when empty', () => {
  const groups = groupByReadiness([{ readiness: 'READY' as const }])
  assert.deepEqual(Object.keys(groups).sort(), ['INFORMATIONAL', 'NEEDS_CLARIFICATION', 'READY'])
  assert.equal(groups.READY.length, 1)
  assert.deepEqual(groups.INFORMATIONAL, [])
})
