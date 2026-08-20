import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'
import { env } from './env'

/**
 * Session token: signing, verification, and the payload shape — SPEC-006 §3.
 *
 * Deliberately free of `next/headers`. The cookie read/write lives in
 * `session-cookie.ts`, so this module — the part with the actual cryptography —
 * is importable and unit-testable outside a request context. Mixing the two put
 * the signature logic behind a framework import that only resolves inside Next,
 * which is precisely the code that most needs a test.
 *
 * A signed cookie rather than a session table: it needs no extra round trip on
 * every request, and the only thing it has to carry is a user id. The cost is that
 * revocation is not instant, which is why the lifetime is short and the payload
 * carries the password's last-changed timestamp — rotating a password invalidates
 * every existing session without a server-side store.
 *
 * Format: `<base64url(payload)>.<base64url(hmac-sha256)>`. HMAC over the exact
 * payload bytes, compared in constant time.
 */

const COOKIE_NAME = 'v2b_session'
const MAX_AGE_SECONDS = 60 * 60 * 24 * 14 // 14 days

export interface SessionPayload {
  /** User id. */
  sub: string
  /** Issued at, epoch seconds. */
  iat: number
  /** Expires at, epoch seconds. */
  exp: number
  /**
   * `passwordUpdatedAt` in epoch **milliseconds** at issue time. A password change
   * moves this forward, so older cookies stop validating — cheap revocation.
   *
   * Milliseconds, not seconds: with second granularity a rotation occurring in the
   * same second as the cookie was issued produced an identical claim, and the stale
   * cookie kept working. That is exactly the window a rapid rotation lands in.
   */
  pwd: number
}

function secret(): Buffer {
  // Reuses APP_ENCRYPTION_KEY's entropy under a distinct label, so there is one
  // secret to manage rather than two, and no key is used for two purposes.
  const raw = env().APP_ENCRYPTION_KEY
  if (!raw) {
    throw new Error(
      'APP_ENCRYPTION_KEY is not set, so sessions cannot be signed. Generate one with: openssl rand -base64 32',
    )
  }
  return createHmac('sha256', Buffer.from(raw, 'base64')).update('session-v1').digest()
}

export function sessionsAvailable(): boolean {
  try {
    secret()
    return true
  } catch {
    return false
  }
}

function sign(payloadB64: string): string {
  return createHmac('sha256', secret()).update(payloadB64).digest('base64url')
}

export function encodeSession(payload: SessionPayload): string {
  const body = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url')
  return `${body}.${sign(body)}`
}

export function decodeSession(token: string): SessionPayload | null {
  const parts = token.split('.')
  if (parts.length !== 2) return null
  const [body, signature] = parts as [string, string]

  let expected: string
  try {
    expected = sign(body)
  } catch {
    return null
  }

  const a = Buffer.from(signature)
  const b = Buffer.from(expected)
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null

  try {
    const payload = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')) as SessionPayload
    if (typeof payload.sub !== 'string' || typeof payload.exp !== 'number') return null
    if (payload.exp * 1000 < Date.now()) return null
    return payload
  } catch {
    return null
  }
}

export function buildSession(userId: string, passwordUpdatedAt: Date | null): SessionPayload {
  const now = Math.floor(Date.now() / 1000)
  return {
    sub: userId,
    iat: now,
    exp: now + MAX_AGE_SECONDS,
    pwd: passwordUpdatedAt ? passwordUpdatedAt.getTime() : 0,
  }
}


/**
 * Cookie attributes.
 *
 * `secure` is derived from the **scheme of `APP_URL`**, not from `NODE_ENV`. That
 * distinction caused a real and confusing bug: the Docker image runs with
 * `NODE_ENV=production` while being served over `http://localhost:3000`, so the cookie
 * went out marked `Secure` and browsers that honour that strictly over http — Safari
 * among them — discarded it silently. Every subsequent request then had no session,
 * fell through to the development identity, and the app showed a *different account's*
 * name to someone who had just signed in.
 *
 * The scheme is the thing that actually determines whether `Secure` is correct, so it
 * is the thing to read. An https deployment still gets `Secure`; a local http one does
 * not, and works.
 */
export function cookieOptions() {
  const overHttps = env().APP_URL.startsWith('https://')
  return {
    httpOnly: true,
    sameSite: 'lax' as const,
    secure: overHttps,
    path: '/',
    maxAge: MAX_AGE_SECONDS,
  }
}

/** Random token for a nonce or a one-time link. */
export function randomToken(bytes = 24): string {
  return randomBytes(bytes).toString('base64url')
}

export { COOKIE_NAME as SESSION_COOKIE_NAME, MAX_AGE_SECONDS as SESSION_MAX_AGE_SECONDS }
