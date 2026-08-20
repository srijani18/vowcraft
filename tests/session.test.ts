import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'

process.env.APP_ENCRYPTION_KEY ??= randomBytes(32).toString('base64')
process.env.DATABASE_URL ??= 'postgresql://test:test@localhost:5432/test'

const { buildSession, decodeSession, encodeSession, sessionsAvailable, SESSION_MAX_AGE_SECONDS } =
  await import('@/lib/session')

const USER = 'usr_abc123'

describe('signed session cookie — SPEC-006 §3', () => {
  test('a token round-trips', () => {
    const payload = buildSession(USER, new Date('2026-08-20T09:00:00.000Z'))
    const decoded = decodeSession(encodeSession(payload))
    assert.equal(decoded?.sub, USER)
    assert.equal(decoded?.pwd, payload.pwd)
  })

  test('the token carries no readable secret and has two parts', () => {
    const token = encodeSession(buildSession(USER, null))
    assert.equal(token.split('.').length, 2)
    assert.ok(!token.includes(process.env.APP_ENCRYPTION_KEY!))
  })

  test('a tampered payload is rejected — the signature covers it', () => {
    const token = encodeSession(buildSession(USER, null))
    const [, signature] = token.split('.')
    const forged = Buffer.from(JSON.stringify({ sub: 'usr_attacker', iat: 0, exp: 9e9, pwd: 0 })).toString(
      'base64url',
    )
    assert.equal(decodeSession(`${forged}.${signature}`), null)
  })

  test('a tampered signature is rejected', () => {
    const [body, signature] = encodeSession(buildSession(USER, null)).split('.')
    const flipped = Buffer.from(signature!, 'base64url')
    flipped[0] = (flipped[0] ?? 0) ^ 0xff
    assert.equal(decodeSession(`${body}.${flipped.toString('base64url')}`), null)
  })

  test('an unsigned token is rejected', () => {
    const body = Buffer.from(JSON.stringify({ sub: USER, iat: 0, exp: 9e9, pwd: 0 })).toString('base64url')
    assert.equal(decodeSession(body), null)
    assert.equal(decodeSession(`${body}.`), null)
  })

  test('malformed input returns null rather than throwing', () => {
    for (const input of ['', 'garbage', 'a.b.c', '....']) {
      assert.equal(decodeSession(input), null, `expected null for ${JSON.stringify(input)}`)
    }
  })

  test('an expired token is rejected even though the signature is valid', () => {
    const now = Math.floor(Date.now() / 1000)
    const expired = encodeSession({ sub: USER, iat: now - 100, exp: now - 1, pwd: 0 })
    assert.equal(decodeSession(expired), null)
  })

  test('the lifetime is 14 days', () => {
    const payload = buildSession(USER, null)
    assert.equal(payload.exp - payload.iat, SESSION_MAX_AGE_SECONDS)
    assert.equal(SESSION_MAX_AGE_SECONDS, 14 * 24 * 60 * 60)
  })

  test('the pwd claim is millisecond-precise — the revocation window', () => {
    // Second granularity had a real hole: a rotation inside the same second as
    // issuance produced an identical claim, so the stale cookie kept working.
    const issued = new Date('2026-08-20T09:00:00.016Z')
    const rotated = new Date('2026-08-20T09:00:00.212Z')
    assert.notEqual(buildSession(USER, issued).pwd, buildSession(USER, rotated).pwd)
    assert.equal(buildSession(USER, issued).pwd, issued.getTime())
  })

  test('an account with no password has a zero claim, not a crash', () => {
    assert.equal(buildSession(USER, null).pwd, 0)
  })

  test('sessions are available when a key is configured', () => {
    assert.equal(sessionsAvailable(), true)
  })
})

describe('cookie attributes — SPEC-006 §3', () => {
  /*
   * The regression this pins. `secure` used to be derived from `NODE_ENV`, and the
   * Docker image runs `NODE_ENV=production` while being served over
   * `http://localhost:3000`. The cookie therefore went out marked `Secure`, browsers
   * that honour that over http discarded it silently, and every request then fell
   * through to the development identity — so someone who had just signed in was shown
   * a *different account's* name. The scheme of APP_URL is what actually determines
   * whether `Secure` is correct, so that is what it reads.
   */
  const withAppUrl = async (url: string) => {
    const { __setEnvForTests } = await import('@/lib/env')
    __setEnvForTests(null)
    process.env.APP_URL = url
    const { cookieOptions } = await import('@/lib/session')
    return cookieOptions()
  }

  test('no Secure flag when served over http', async () => {
    const options = await withAppUrl('http://localhost:3000')
    assert.equal(options.secure, false, 'a Secure cookie over http is silently dropped')
  })

  test('Secure is set when served over https', async () => {
    assert.equal((await withAppUrl('https://vowcraft.example')).secure, true)
  })

  test('a trailing path on APP_URL does not confuse the check', async () => {
    assert.equal((await withAppUrl('https://vowcraft.example/app')).secure, true)
    assert.equal((await withAppUrl('http://localhost:3000/')).secure, false)
  })

  test('httpOnly and SameSite are always set, regardless of scheme', async () => {
    for (const url of ['http://localhost:3000', 'https://vowcraft.example']) {
      const options = await withAppUrl(url)
      assert.equal(options.httpOnly, true, 'the session cookie must not be readable by script')
      assert.equal(options.sameSite, 'lax')
      assert.equal(options.path, '/')
      assert.equal(options.maxAge, SESSION_MAX_AGE_SECONDS)
    }
  })
})
