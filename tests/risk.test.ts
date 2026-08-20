import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { classifyRisk, effectiveGate, gateSatisfied, largestAmount } from '@/domain/risk'
import { item, settings, validCalendarPayload } from './fixtures'

const SELF = 'me@acme.test'

describe('classifyRisk — SPEC-003 §3', () => {
  test('a draft email is LOW: nothing is sent', () => {
    const payload = { to: ['outsider@vendor.example'], subject: 'Hi', body: 'x', sendMode: 'draft' }
    const risk = classifyRisk(item({ actionType: 'EMAIL' }), payload, settings, SELF)
    assert.equal(risk.tier, 'LOW')
    assert.ok(risk.factors.some((f) => /draft/i.test(f)))
  })

  test('sending to colleagues only is MEDIUM', () => {
    const payload = { to: ['priya@acme.test'], subject: 'Hi', body: 'x', sendMode: 'send' }
    const risk = classifyRisk(item({ actionType: 'EMAIL' }), payload, settings, SELF)
    assert.equal(risk.tier, 'MEDIUM')
  })

  test('one external recipient makes the whole thing HIGH', () => {
    const payload = {
      to: ['priya@acme.test', 'accounts@vendor.example'],
      subject: 'Hi',
      body: 'x',
      sendMode: 'send',
    }
    const risk = classifyRisk(item({ actionType: 'EMAIL' }), payload, settings, SELF)
    assert.equal(risk.tier, 'HIGH')
    assert.ok(risk.factors.some((f) => f.includes('accounts@vendor.example')))
  })

  test('cc and bcc count as recipients too', () => {
    const payload = {
      to: ['priya@acme.test'],
      bcc: ['leak@vendor.example'],
      subject: 'Hi',
      body: 'x',
      sendMode: 'send',
    }
    assert.equal(classifyRisk(item({ actionType: 'EMAIL' }), payload, settings, SELF).tier, 'HIGH')
  })

  test('a subdomain of an org domain is still internal', () => {
    const payload = { to: ['bot@mail.acme.test'], subject: 'Hi', body: 'x', sendMode: 'send' }
    assert.equal(classifyRisk(item({ actionType: 'EMAIL' }), payload, settings, SELF).tier, 'MEDIUM')
  })

  test('a lookalike domain is NOT internal', () => {
    // `notacme.test` must not match `acme.test` by suffix.
    const payload = { to: ['x@notacme.test'], subject: 'Hi', body: 'x', sendMode: 'send' }
    assert.equal(classifyRisk(item({ actionType: 'EMAIL' }), payload, settings, SELF).tier, 'HIGH')
  })

  test('with no org domains configured, everyone is external', () => {
    const bare = { ...settings, orgDomains: [] }
    const payload = { to: ['priya@acme.test'], subject: 'Hi', body: 'x', sendMode: 'send' }
    assert.equal(classifyRisk(item({ actionType: 'EMAIL' }), payload, bare, SELF).tier, 'HIGH')
  })

  test('a calendar hold with only yourself is LOW', () => {
    const risk = classifyRisk(
      item({ actionType: 'CALENDAR' }),
      { ...validCalendarPayload, attendees: [SELF] },
      settings,
      SELF,
    )
    assert.equal(risk.tier, 'LOW')
  })

  test('inviting anyone else is MEDIUM', () => {
    const risk = classifyRisk(item({ actionType: 'CALENDAR' }), validCalendarPayload, settings, SELF)
    assert.equal(risk.tier, 'MEDIUM')
    assert.ok(risk.factors.some((f) => /1 other person/.test(f)))
  })

  test('a task for yourself is LOW; for someone else, MEDIUM', () => {
    const mine = classifyRisk(item({ actionType: 'TASK' }), { title: 'x', assignee: SELF }, settings, SELF)
    assert.equal(mine.tier, 'LOW')
    const theirs = classifyRisk(
      item({ actionType: 'TASK' }),
      { title: 'x', assignee: 'priya@acme.test' },
      settings,
      SELF,
    )
    assert.equal(theirs.tier, 'MEDIUM')
  })

  test('a destructive verb escalates to HIGH from any starting tier', () => {
    const risk = classifyRisk(
      item({ actionType: 'TASK', description: 'Delete the staging database' }),
      { title: 'x', assignee: SELF },
      settings,
      SELF,
    )
    assert.equal(risk.tier, 'HIGH')
    assert.ok(risk.factors.some((f) => /destructive|irreversible/i.test(f)))
  })

  test('money above the limit escalates, and the factor names the amount', () => {
    const risk = classifyRisk(
      item({ actionType: 'TASK', description: 'Approve the $25,000 vendor renewal' }),
      { title: 'x', assignee: SELF },
      settings,
      SELF,
    )
    assert.equal(risk.tier, 'HIGH')
    assert.ok(risk.factors.some((f) => f.includes('25,000')))
  })

  test('money below the limit does not escalate', () => {
    const risk = classifyRisk(
      item({ actionType: 'TASK', description: 'Approve the $40 lunch order' }),
      { title: 'x', assignee: SELF },
      settings,
      SELF,
    )
    assert.equal(risk.tier, 'LOW')
  })

  test('escalation only — a LOW signal never pulls a HIGH action back down', () => {
    // Draft (LOW) plus a destructive verb (HIGH) must land on HIGH.
    const risk = classifyRisk(
      item({ actionType: 'EMAIL', description: 'Wipe the archive and email the team' }),
      { to: [SELF], subject: 'x', body: 'y', sendMode: 'draft' },
      settings,
      SELF,
    )
    assert.equal(risk.tier, 'HIGH')
  })

  test('NONE carries no risk and no factors', () => {
    const risk = classifyRisk(item({ actionType: 'NONE', description: 'Note: all fine' }), {}, settings, SELF)
    assert.equal(risk.tier, 'LOW')
    assert.deepEqual(risk.factors, [])
  })
})

describe('largestAmount', () => {
  test('reads currency symbols, codes, and magnitude suffixes', () => {
    assert.equal(largestAmount('costs $1,200 total'), 1200)
    assert.equal(largestAmount('about €900'), 900)
    assert.equal(largestAmount('USD 2,500 per month'), 2500)
    assert.equal(largestAmount('a $40k contract'), 40_000)
    assert.equal(largestAmount('$1.5 million upfront'), 1_500_000)
  })

  test('takes the largest of several amounts', () => {
    assert.equal(largestAmount('either $500 or $12,000'), 12_000)
  })

  test('returns null when nothing is monetary', () => {
    assert.equal(largestAmount('meet at 3pm with 12 people'), null)
    assert.equal(largestAmount(''), null)
  })
})

describe('effectiveGate — SPEC-003 §5', () => {
  test('HIGH always demands a typed confirmation, even at HIGH confidence', () => {
    const { gate } = effectiveGate('HIGH', 'HIGH', { ...settings, autoExecuteLowRisk: true }, 'EMAIL')
    assert.equal(gate, 'EXPLICIT_APPROVAL_WITH_CONFIRMATION')
  })

  test('MEDIUM always needs explicit approval', () => {
    assert.equal(effectiveGate('MEDIUM', 'HIGH', settings, 'CALENDAR').gate, 'EXPLICIT_APPROVAL')
  })

  test('LOW + HIGH confidence auto-executes only when opted in', () => {
    assert.equal(effectiveGate('LOW', 'HIGH', settings, 'TASK').gate, 'EXPLICIT_APPROVAL')
    const opted = { ...settings, autoExecuteLowRisk: true }
    assert.equal(effectiveGate('LOW', 'HIGH', opted, 'TASK').gate, 'AUTO')
  })

  test('LOW + MEDIUM confidence never auto-executes, even opted in', () => {
    const opted = { ...settings, autoExecuteLowRisk: true }
    assert.equal(effectiveGate('LOW', 'MEDIUM', opted, 'TASK').gate, 'EXPLICIT_APPROVAL')
  })

  test('LOW confidence adds an uncertainty note', () => {
    const { gate, note } = effectiveGate('LOW', 'LOW', settings, 'TASK')
    assert.equal(gate, 'EXPLICIT_APPROVAL')
    assert.match(note!, /unsure/i)
  })

  test('a per-type threshold can only tighten the gate', () => {
    const tightened = {
      ...settings,
      autoExecuteLowRisk: true,
      approvalThresholds: { TASK: 'EXPLICIT_APPROVAL_WITH_CONFIRMATION' as const },
    }
    assert.equal(effectiveGate('LOW', 'HIGH', tightened, 'TASK').gate, 'EXPLICIT_APPROVAL_WITH_CONFIRMATION')
  })

  test('a per-type threshold cannot loosen it', () => {
    const loosened = { ...settings, approvalThresholds: { EMAIL: 'AUTO' as const } }
    // HIGH risk must survive an attempt to configure it down to AUTO.
    assert.equal(effectiveGate('HIGH', 'HIGH', loosened, 'EMAIL').gate, 'EXPLICIT_APPROVAL_WITH_CONFIRMATION')
  })
})

describe('gateSatisfied', () => {
  test('AUTO needs no recorded decision', () => {
    assert.ok(gateSatisfied('AUTO', 'PROPOSED'))
  })

  test('approval gates require APPROVED or FAILED', () => {
    assert.ok(gateSatisfied('EXPLICIT_APPROVAL', 'APPROVED'))
    // FAILED is included so retrying a transient error does not need re-approval.
    assert.ok(gateSatisfied('EXPLICIT_APPROVAL', 'FAILED'))
    assert.ok(!gateSatisfied('EXPLICIT_APPROVAL', 'PROPOSED'))
    assert.ok(!gateSatisfied('EXPLICIT_APPROVAL', 'DEFERRED'))
    assert.ok(!gateSatisfied('EXPLICIT_APPROVAL_WITH_CONFIRMATION', 'PROPOSED'))
  })
})
