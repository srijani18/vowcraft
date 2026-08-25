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

describe('utterances — SPEC-013\'s per-utterance timestamps', () => {
  test('a final frame with atMs is recorded as an utterance', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'first thing said', final: true }, 1000)
    // `speaker: null` rather than absent: the field is always present so a consumer can
    // distinguish "nobody attributed this" from speaker index 0.
    assert.deepEqual(acc.utterances, [{ text: 'first thing said', atMs: 1000, speaker: null }])
  })

  test('an interim frame never appears in utterances even with atMs passed', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'still speaking', final: false }, 500)
    assert.deepEqual(acc.utterances, [])
  })

  test('a final frame with no atMs is committed but not added as an utterance', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'no timestamp', final: true })
    assert.equal(acc.finalText, 'no timestamp')
    assert.deepEqual(acc.utterances, [])
  })

  test('successive utterances keep their own timestamps in order', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'first', final: true }, 1000)
    acc.add({ text: 'second', final: true }, 2500)
    assert.deepEqual(acc.utterances, [
      { text: 'first', atMs: 1000, speaker: null },
      { text: 'second', atMs: 2500, speaker: null },
    ])
  })

  test('a diarized frame carries its speaker index through', () => {
    // The whole point of SPEC-011 for a live meeting: the relay forwards Deepgram's
    // speaker index, and this is the only place it is retained between the socket and the
    // POST that builds segments.
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'shall we start', final: true, speaker: 0 }, 1000)
    acc.add({ text: 'yes go ahead', final: true, speaker: 1 }, 2000)
    assert.deepEqual(acc.utterances, [
      { text: 'shall we start', atMs: 1000, speaker: 0 },
      { text: 'yes go ahead', atMs: 2000, speaker: 1 },
    ])
  })

  test('speaker index 0 is kept, not treated as absent', () => {
    // Deepgram numbers speakers from 0, so a falsy check here would silently drop every
    // attribution belonging to the first speaker.
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'first speaker', final: true, speaker: 0 }, 500)
    // deepEqual on the whole array rather than indexing, which also proves nothing else
    // was recorded — and keeps the assertion honest under `noUncheckedIndexedAccess`.
    assert.deepEqual(acc.utterances, [{ text: 'first speaker', atMs: 500, speaker: 0 }])
  })

  test('reset clears utterances too', () => {
    const acc = new TranscriptAccumulator()
    acc.add({ text: 'first session', final: true }, 1000)
    acc.reset()
    assert.deepEqual(acc.utterances, [])
  })
})
