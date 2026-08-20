import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import {
  emailDomain,
  fieldLabel,
  isValidEmail,
  missingFields,
  PAYLOAD_SCHEMA,
  toEmailList,
} from '@/domain/payload'

describe('missingFields — SPEC-001 §6.1', () => {
  test('reports required fields in declaration order', () => {
    assert.deepEqual(missingFields('CALENDAR', {}), ['title', 'startsAt', 'durationMinutes', 'attendees'])
  })

  test('an empty string does not count as provided', () => {
    assert.deepEqual(missingFields('TASK', { title: '   ' }), ['title'])
  })

  test('an empty array does not satisfy a minItems field', () => {
    const missing = missingFields('EMAIL', { to: [], subject: 'Hi', body: 'x' })
    assert.deepEqual(missing, ['to'])
  })

  test('zero is a legitimate number, not an absence', () => {
    const missing = missingFields('CALENDAR', {
      title: 'x',
      startsAt: 'now',
      durationMinutes: 0,
      attendees: ['a@b.co'],
    })
    assert.ok(!missing.includes('durationMinutes'))
  })

  test('NaN is not a number', () => {
    const missing = missingFields('CALENDAR', {
      title: 'x',
      startsAt: 'now',
      durationMinutes: Number.NaN,
      attendees: ['a@b.co'],
    })
    assert.ok(missing.includes('durationMinutes'))
  })

  test('optional fields are never reported', () => {
    assert.deepEqual(missingFields('TASK', { title: 'Update the deck' }), [])
  })

  test('NONE requires nothing', () => {
    assert.deepEqual(missingFields('NONE', {}), [])
  })

  test('every action type has a schema entry, so the table is total', () => {
    for (const type of ['CALENDAR', 'TASK', 'EMAIL', 'REMINDER', 'NONE'] as const) {
      assert.ok(Array.isArray(PAYLOAD_SCHEMA[type]), `${type} has no payload schema`)
    }
  })

  test('fieldLabel falls back to the key rather than throwing', () => {
    assert.equal(fieldLabel('CALENDAR', 'startsAt'), 'Starts at')
    assert.equal(fieldLabel('CALENDAR', 'nonsense'), 'nonsense')
  })
})

describe('email helpers', () => {
  test('toEmailList splits, trims, lowercases, and dedupes', () => {
    assert.deepEqual(toEmailList(' A@x.com, b@x.com ;A@X.com '), ['a@x.com', 'b@x.com'])
  })

  test('toEmailList accepts an array as readily as a string', () => {
    assert.deepEqual(toEmailList(['A@x.com', 'a@x.com']), ['a@x.com'])
  })

  test('toEmailList returns empty for anything unusable', () => {
    assert.deepEqual(toEmailList(null), [])
    assert.deepEqual(toEmailList(42), [])
    assert.deepEqual(toEmailList([1, 2]), [])
  })

  test('isValidEmail is permissive but not credulous', () => {
    assert.ok(isValidEmail('a.b+tag@sub.example.co'))
    assert.ok(!isValidEmail('no-at-sign'))
    assert.ok(!isValidEmail('two@@at.com'))
    assert.ok(!isValidEmail('trailing@dot.'))
    assert.ok(!isValidEmail(''))
  })

  test('emailDomain takes the last @, so quoted locals do not confuse it', () => {
    assert.equal(emailDomain('a@b@example.com'), 'example.com')
    assert.equal(emailDomain('nodomain'), null)
  })
})

