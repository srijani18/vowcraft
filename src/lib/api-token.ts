import { createHmac, timingSafeEqual } from 'node:crypto'

/**
 * HS256 token minting — the pure half of `api-server.ts`, split out the same way
 * `session.ts` is split from `session-cookie.ts`: no framework or database import,
 * so the signing logic is unit-testable without a request context.
 *
 * Produces tokens matching `apps/api/app/core/security.py::issue_token` exactly —
 * same claim names, the `pwd`-in-milliseconds revocation convention, and HS256
 * signing over the shared secret's raw UTF-8 bytes (PyJWT does not base64-decode
 * the key). Verified byte-for-byte against `decode_token` in
 * `apps/api/tests/test_web_minted_tokens.py`.
 */

export interface ApiTokenUser {
  id: string
  pwdEpochMs: number
}

function jwtSecret(): string {
  const secret = process.env.JWT_SECRET
  if (!secret) {
    throw new Error('JWT_SECRET is not set; the server cannot call the FastAPI backend.')
  }
  return secret
}

function jwtAlgorithm(): string {
  return process.env.JWT_ALGORITHM || 'HS256'
}

function base64url(input: Buffer): string {
  return input.toString('base64url')
}

export function mintApiAccessToken(user: ApiTokenUser, ttlSeconds = 60): string {
  if (jwtAlgorithm() !== 'HS256') {
    // The only algorithm either side has ever configured. Fail loudly rather than
    // silently signing with the wrong algorithm if that ever changes on one side only.
    throw new Error(`Unsupported JWT_ALGORITHM for web-minted tokens: ${jwtAlgorithm()}`)
  }
  const now = Math.floor(Date.now() / 1000)
  const header = { alg: 'HS256', typ: 'JWT' }
  const payload = {
    sub: user.id,
    typ: 'access',
    pwd: user.pwdEpochMs,
    iat: now,
    exp: now + ttlSeconds,
  }
  const headerB64 = base64url(Buffer.from(JSON.stringify(header), 'utf8'))
  const payloadB64 = base64url(Buffer.from(JSON.stringify(payload), 'utf8'))
  const signingInput = `${headerB64}.${payloadB64}`
  const signature = createHmac('sha256', Buffer.from(jwtSecret(), 'utf8')).update(signingInput).digest()
  return `${signingInput}.${base64url(signature)}`
}

/** Test seam: lets a suite check the signature without re-deriving the HMAC itself. */
export function verifyApiAccessTokenForTests(token: string): boolean {
  const parts = token.split('.')
  if (parts.length !== 3) return false
  const [headerB64, payloadB64, signatureB64] = parts as [string, string, string]
  const expected = createHmac('sha256', Buffer.from(jwtSecret(), 'utf8'))
    .update(`${headerB64}.${payloadB64}`)
    .digest()
  const actual = Buffer.from(signatureB64, 'base64url')
  return actual.length === expected.length && timingSafeEqual(actual, expected)
}
