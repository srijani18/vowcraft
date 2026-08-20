/**
 * Bounded retry with full jitter — SPEC-002 §6.
 *
 * Full jitter rather than plain exponential backoff: when a provider has an
 * outage and many items are executed at once, synchronised retries would
 * reproduce the thundering herd that caused the outage.
 */

export interface RetryPolicy {
  attempts: number
  baseMs: number
  maxDelayMs: number
  /** Only failures this returns true for are retried. */
  isRetryable(err: unknown): boolean
  /** Provider-supplied delay, honoured when shorter than `maxHonouredRetryAfterMs`. */
  retryAfterMs?(err: unknown): number | undefined
  onAttempt?(info: { attempt: number; delayMs: number; err: unknown }): void
  sleep?(ms: number): Promise<void>
  random?(): number
}

export const DEFAULT_POLICY = {
  attempts: 3,
  baseMs: 400,
  maxDelayMs: 8_000,
  maxHonouredRetryAfterMs: 30_000,
} as const

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

export function backoffDelay(
  attempt: number,
  policy: Pick<RetryPolicy, 'baseMs' | 'maxDelayMs'>,
  random: () => number = Math.random,
): number {
  const ceiling = Math.min(policy.maxDelayMs, policy.baseMs * 2 ** (attempt - 1))
  // Full jitter: uniform over [ceiling/2, ceiling].
  return Math.round(ceiling * (0.5 + random() * 0.5))
}

export interface AttemptRecord {
  n: number
  outcome: 'SUCCESS' | 'FAILED'
  durationMs: number
  errorCode?: string
  errorMessage?: string
}

export interface RetryOutcome<T> {
  value?: T
  error?: unknown
  attempts: AttemptRecord[]
}

/**
 * Runs `fn` up to `policy.attempts` times. Always resolves — the caller decides
 * what a terminal failure means, because for a mutating provider that decision
 * depends on whether the side effect may already exist (SPEC-002 §6).
 */
export async function retry<T>(
  fn: (attempt: number) => Promise<T>,
  policy: RetryPolicy,
): Promise<RetryOutcome<T>> {
  const sleep = policy.sleep ?? wait
  const random = policy.random ?? Math.random
  const records: AttemptRecord[] = []

  for (let attempt = 1; attempt <= policy.attempts; attempt++) {
    const started = Date.now()
    try {
      const value = await fn(attempt)
      records.push({ n: attempt, outcome: 'SUCCESS', durationMs: Date.now() - started })
      return { value, attempts: records }
    } catch (err) {
      const e = err as { code?: string; message?: string }
      records.push({
        n: attempt,
        outcome: 'FAILED',
        durationMs: Date.now() - started,
        errorCode: e?.code,
        errorMessage: e?.message,
      })

      const isLast = attempt === policy.attempts
      if (isLast || !policy.isRetryable(err)) return { error: err, attempts: records }

      const suggested = policy.retryAfterMs?.(err)
      const delayMs =
        suggested !== undefined && suggested <= DEFAULT_POLICY.maxHonouredRetryAfterMs
          ? suggested
          : backoffDelay(attempt, policy, random)

      policy.onAttempt?.({ attempt, delayMs, err })
      await sleep(delayMs)
    }
  }

  // Unreachable: the loop always returns. Kept so the signature stays total.
  return { error: new Error('retry exhausted'), attempts: records }
}

/** Rejects with an AbortError-shaped failure if `promise` outruns `ms`. */
export async function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(`${label} timed out after ${ms}ms`) as Error & { code: string }
      err.code = 'timeout'
      reject(err)
    }, ms)
  })
  try {
    return await Promise.race([promise, timeout])
  } finally {
    clearTimeout(timer!)
  }
}
