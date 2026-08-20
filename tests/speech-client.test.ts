import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { TranscriptAccumulator } from '@/lib/speech-client'

/**
 * Transcript accumulation — SPEC-014 §3.
 *
 * The vendor-shape and auth-mode tests that used to sit here were deleted with the code
 * they covered: the browser no longer speaks to a provider, so there is no frame shape or
 * auth mode on this side to get wrong. That responsibility moved to the FastAPI relay and
 * is tested there.
 *
 * What is left is the interim/final bookkeeping, which is where the visible bugs live.
 */

describe('accumulation — interim frames replace, finals commit', () => {
  test('an interim guess does not accumulate as it is refined', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'we', final: false })
    acc.add({ text: 'we need', final: false })
    acc.add({ text: 'we need audit', final: false })
    // The bug this guards against is "we we need we need audit".
    assert.equal(acc.displayText, 'we need audit')
    assert.equal(acc.finalText, '')
  })

  test('a final frame commits, and the next interim appends after it', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'we need audit logging.', final: true })
    acc.add({ text: 'it should', final: false })
    assert.equal(acc.finalText, 'we need audit logging.')
    assert.equal(acc.displayText, 'we need audit logging. it should')
  })

  test('successive finals join in order', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'First requirement.', final: true })
    acc.add({ text: 'Second requirement.', final: true })
    assert.equal(acc.finalText, 'First requirement. Second requirement.')
  })

  test('an interim left dangling at stop is excluded from what is generated from', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'A committed sentence.', final: true })
    acc.add({ text: 'a half-heard fragm', final: false })
    // displayText shows it, finalText does not: generating from an unconfirmed fragment
    // would put the provider's half-guess into the document.
    assert.match(acc.displayText, /half-heard/)
    assert.equal(acc.finalText, 'A committed sentence.')
  })

  test('reset clears both, so a second recording does not inherit the first', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'first session', final: true })
    acc.reset()
    assert.equal(acc.finalText, '')
    assert.equal(acc.displayText, '')
    assert.equal(acc.isEmpty, true)
  })

  test('isEmpty is true before anything and false after an interim alone', () => {
    const acc = new TranscriptAccumulator()
    assert.equal(acc.isEmpty, true)
    acc.add({ text: 'something', final: false })
    assert.equal(acc.isEmpty, false)
  })

  test('collapses the whitespace a stream of frames introduces', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: '  spaced   out  ', final: true })
    acc.add({ text: '  again  ', final: true })
    assert.equal(acc.finalText, 'spaced out again')
  })
})
