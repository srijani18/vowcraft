import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { backoffDelay, DEFAULT_POLICY, retry, withTimeout } from '@/lib/retry'

const policy = { baseMs: 400, maxDelayMs: 8_000 }

describe('backoff with full jitter — SPEC-002 §6', () => {
  test('a delay always lands in [ceiling/2, ceiling]', () => {
    for (const attempt of [1, 2, 3, 4, 5]) {
      const ceiling = Math.min(policy.maxDelayMs, policy.baseMs * 2 ** (attempt - 1))
      for (const random of [0, 0.25, 0.5, 0.99, 1]) {
        const delay = backoffDelay(attempt, policy, () => random)
        assert.ok(
          delay >= Math.floor(ceiling / 2) && delay <= ceiling,
          `attempt ${attempt}, random ${random}: ${delay} outside [${ceiling / 2}, ${ceiling}]`,
        )
      }
    }
  })

  test('the ceiling grows exponentially then flattens at the cap', () => {
    const at = (n: number) => backoffDelay(n, policy, () => 1)
    assert.equal(at(1), 400)
    assert.equal(at(2), 800)
    assert.equal(at(3), 1_600)
    assert.equal(at(10), 8_000)
    assert.equal(at(20), 8_000)
  })

  test('jitter actually varies — not exponential backoff by another name', () => {
    // A synchronised herd is the failure mode this exists to prevent.
    const delays = new Set(Array.from({ length: 40 }, () => backoffDelay(4, policy)))
    assert.ok(delays.size > 5, `expected spread, got ${delays.size} distinct values`)
  })
})

describe('retry', () => {
  const base = { attempts: 3, baseMs: 1, maxDelayMs: 2, sleep: async () => {} }

  test('a first-try success runs once', async () => {
    let calls = 0
    const outcome = await retry(
      async () => {
        calls += 1
        return 'ok'
      },
      { ...base, isRetryable: () => true },
    )
    assert.equal(outcome.value, 'ok')
    assert.equal(calls, 1)
    assert.equal(outcome.attempts.length, 1)
    assert.equal(outcome.attempts[0]!.outcome, 'SUCCESS')
  })

  test('a retryable failure is retried up to the limit, then reported', async () => {
    let calls = 0
    const outcome = await retry(
      async () => {
        calls += 1
        throw Object.assign(new Error('503'), { code: 'upstream' })
      },
      { ...base, isRetryable: () => true },
    )
    assert.equal(calls, 3)
    assert.equal(outcome.value, undefined)
    assert.equal(outcome.attempts.length, 3)
    assert.ok(outcome.attempts.every((a) => a.outcome === 'FAILED'))
    assert.equal(outcome.attempts[0]!.errorCode, 'upstream')
  })

  test('a non-retryable failure stops after one attempt', async () => {
    let calls = 0
    const outcome = await retry(
      async () => {
        calls += 1
        throw new Error('400 bad request')
      },
      { ...base, isRetryable: () => false },
    )
    assert.equal(calls, 1, 'a malformed request is malformed on the third try too')
    assert.equal(outcome.attempts.length, 1)
  })

  test('a late success is kept, and the earlier failures are still recorded', async () => {
    let calls = 0
    const outcome = await retry(
      async () => {
        calls += 1
        if (calls < 3) throw new Error('flaky')
        return 'eventually'
      },
      { ...base, isRetryable: () => true },
    )
    assert.equal(outcome.value, 'eventually')
    assert.equal(outcome.attempts.length, 3)
    assert.deepEqual(
      outcome.attempts.map((a) => a.outcome),
      ['FAILED', 'FAILED', 'SUCCESS'],
    )
  })

  test('the attempt number is passed through, so a provider can vary its call', async () => {
    const seen: number[] = []
    await retry(
      async (attempt) => {
        seen.push(attempt)
        throw new Error('again')
      },
      { ...base, isRetryable: () => true },
    )
    assert.deepEqual(seen, [1, 2, 3])
  })

  test('a short Retry-After overrides the computed delay', async () => {
    const delays: number[] = []
    await retry(
      async () => {
        throw new Error('429')
      },
      {
        ...base,
        baseMs: 5_000,
        maxDelayMs: 8_000,
        isRetryable: () => true,
        retryAfterMs: () => 250,
        onAttempt: ({ delayMs }) => delays.push(delayMs),
      },
    )
    assert.deepEqual(delays, [250, 250])
  })

  test('an absurd Retry-After is ignored in favour of the computed delay', async () => {
    const delays: number[] = []
    await retry(
      async () => {
        throw new Error('429')
      },
      {
        ...base,
        isRetryable: () => true,
        // Well past the 30s ceiling: a provider must not be able to stall us.
        retryAfterMs: () => 10 * 60_000,
        onAttempt: ({ delayMs }) => delays.push(delayMs),
      },
    )
    assert.ok(delays.every((d) => d <= 2), `expected computed delays, got ${delays.join(', ')}`)
  })

  test('retry never rejects — the caller decides what failure means', async () => {
    // For a mutating provider that decision depends on whether the side effect
    // may already exist, which only the caller knows.
    const outcome = await retry(
      async () => {
        throw new Error('boom')
      },
      { ...base, isRetryable: () => true },
    )
    assert.ok(outcome.error instanceof Error)
    assert.equal(outcome.value, undefined)
  })

  test('the shipped policy is three attempts with a jittered ceiling', () => {
    assert.equal(DEFAULT_POLICY.attempts, 3)
    assert.equal(DEFAULT_POLICY.baseMs, 400)
    assert.equal(DEFAULT_POLICY.maxDelayMs, 8_000)
    assert.equal(DEFAULT_POLICY.maxHonouredRetryAfterMs, 30_000)
  })
})

describe('withTimeout', () => {
  test('a fast promise passes through', async () => {
    assert.equal(await withTimeout(Promise.resolve('quick'), 1_000, 'test'), 'quick')
  })

  test('a slow promise rejects with a timeout code and a useful label', async () => {
    await assert.rejects(
      () => withTimeout(new Promise((resolve) => setTimeout(resolve, 200)), 20, 'Google Calendar execution'),
      (err: Error & { code?: string }) => {
        assert.equal(err.code, 'timeout')
        assert.match(err.message, /Google Calendar execution timed out after 20ms/)
        return true
      },
    )
  })

  test('an underlying rejection is surfaced rather than masked as a timeout', async () => {
    await assert.rejects(() => withTimeout(Promise.reject(new Error('real error')), 1_000, 'test'), /real error/)
  })
})
