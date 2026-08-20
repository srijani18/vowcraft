import { ProviderError, isRetryableStatus } from './types'

/**
 * Shared fetch wrapper for provider adapters. Every non-2xx becomes a
 * ProviderError whose `retryable` flag is decided here, in one place, so no
 * adapter invents its own retry semantics.
 */

export interface ProviderFetchInit extends RequestInit {
  /** Adapter name used in error codes and messages. */
  provider: string
  timeoutMs?: number
  /** Non-idempotent calls report `uncertain: true` on a post-dispatch timeout. */
  idempotent?: boolean
}

function parseRetryAfter(header: string | null): number | undefined {
  if (!header) return undefined
  const seconds = Number(header)
  if (Number.isFinite(seconds)) return seconds * 1000
  const at = Date.parse(header)
  return Number.isFinite(at) ? Math.max(0, at - Date.now()) : undefined
}

/**
 * As `providerFetch`, but resolves the raw Response. Needed by providers that
 * answer `202 Accepted` with an empty body and put the useful identifier in a
 * header — SendGrid being the one that does.
 */
export async function providerFetchRaw(url: string, init: ProviderFetchInit): Promise<Response> {
  return runFetch(url, init)
}

export async function providerFetch<T>(url: string, init: ProviderFetchInit): Promise<T> {
  const response = await runFetch(url, init)
  if (response.status === 204 || response.status === 202) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

async function runFetch(url: string, init: ProviderFetchInit): Promise<Response> {
  const { provider, timeoutMs = 15_000, idempotent = true, ...rest } = init
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  let response: Response
  try {
    response = await fetch(url, { ...rest, signal: controller.signal })
  } catch (err) {
    const aborted = (err as Error).name === 'AbortError'
    throw new ProviderError({
      code: aborted ? 'timeout' : 'network_error',
      message: aborted
        ? `${provider} did not respond within ${timeoutMs}ms.`
        : `${provider} was unreachable: ${(err as Error).message}`,
      // A network failure on a non-idempotent write may still have landed.
      retryable: idempotent,
      uncertain: !idempotent,
    })
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new ProviderError({
      code: `${provider}_http_${response.status}`,
      message: `${provider} returned ${response.status}: ${body.slice(0, 400) || response.statusText}`,
      status: response.status,
      retryable: isRetryableStatus(response.status),
      retryAfterMs: parseRetryAfter(response.headers.get('retry-after')),
    })
  }

  return response
}

/** Deterministic pseudo-id for mock mode, stable for a given key. */
export function mockId(prefix: string, key: string): string {
  let hash = 2166136261
  for (let i = 0; i < key.length; i++) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return `${prefix}_mock_${(hash >>> 0).toString(16).padStart(8, '0')}`
}

/** Mock latency, so loading states are exercised rather than skipped. */
export const simulateLatency = () => new Promise<void>((r) => setTimeout(r, 90 + Math.random() * 80))
