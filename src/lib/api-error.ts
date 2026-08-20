/**
 * Reads the message out of either shape a route can answer with.
 *
 * `handle()` returns a service result verbatim on success, and wraps a *thrown* error as
 * `{ error: { code, message } }`. A service that reports a handled failure in its own
 * result — extraction, which must still return the transcript — therefore puts a plain
 * string in `error`, while a 404 from the same route puts an object there. Client code
 * that reads only one shape silently shows no message for the other; the extraction retry
 * button did exactly that.
 */
export function apiMessage(body: unknown, fallback: string): string {
  if (typeof body !== 'object' || body === null) return fallback
  const error = (body as { error?: unknown }).error
  if (typeof error === 'string' && error.trim()) return error
  if (typeof error === 'object' && error !== null) {
    const message = (error as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return fallback
}

/** The machine-readable reason, from either shape. */
export function apiErrorCode(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  const direct = (body as { errorCode?: unknown }).errorCode
  if (typeof direct === 'string') return direct
  const error = (body as { error?: unknown }).error
  if (typeof error === 'object' && error !== null) {
    const code = (error as { code?: unknown }).code
    if (typeof code === 'string') return code
  }
  return null
}
