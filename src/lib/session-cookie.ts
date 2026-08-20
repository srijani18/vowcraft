import { cookies } from 'next/headers'
import {
  cookieOptions,
  decodeSession,
  encodeSession,
  SESSION_COOKIE_NAME,
  type SessionPayload,
} from './session'

/* Re-exported: `cookieOptions` is pure (it reads only APP_URL) and lives in
 * `session.ts` so it can be unit-tested without a request context. Callers that
 * already import from here should not need a second import. */
export { cookieOptions }

/**
 * Reading and writing the session cookie — the request-bound half of SPEC-006 §3.
 *
 * Separated from `session.ts` so the signing and verification logic can be tested
 * without a request context. Everything here is a thin wrapper over `cookies()`.
 */

export async function setSessionCookie(payload: SessionPayload): Promise<void> {
  const store = await cookies()
  store.set(SESSION_COOKIE_NAME, encodeSession(payload), cookieOptions())
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies()
  store.set(SESSION_COOKIE_NAME, '', { ...cookieOptions(), maxAge: 0 })
}

export async function readSessionCookie(): Promise<SessionPayload | null> {
  const store = await cookies()
  const token = store.get(SESSION_COOKIE_NAME)?.value
  return token ? decodeSession(token) : null
}
