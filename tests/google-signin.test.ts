import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'

process.env.APP_ENCRYPTION_KEY ??= randomBytes(32).toString('base64')
process.env.DATABASE_URL ??= 'postgresql://test:test@localhost:5432/test'
process.env.GOOGLE_CLIENT_ID = 'our-client-id.apps.googleusercontent.com'
process.env.GOOGLE_CLIENT_SECRET = 'test-secret'

const { googleConfigured, verifyIdTokenClaims } = await import('@/server/auth/google')

/**
 * The id_token claim checks — SPEC-007 §3.2.
 *
 * These are the security-critical lines in Google sign-in. The signature is not
 * verified (the token arrives over TLS from Google's own token endpoint, which is
 * the documented exemption), so these claim checks are what stand between us and a
 * token that is genuine but issued for somebody else's application.
 */

const NONCE = 'test-nonce-value'
const FUTURE = Math.floor(Date.now() / 1000) + 3600
const PAST = Math.floor(Date.now() / 1000) - 3600

function idToken(claims: Record<string, unknown>): string {
  const part = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url')
  // A real-looking three-part JWT. The signature is arbitrary because it is not
  // checked — which is exactly why the claims must be.
  return `${part({ alg: 'RS256', typ: 'JWT' })}.${part(claims)}.${Buffer.from('sig').toString('base64url')}`
}

const validClaims = {
  iss: 'https://accounts.google.com',
  aud: 'our-client-id.apps.googleusercontent.com',
  sub: '1029384756',
  exp: FUTURE,
  nonce: NONCE,
  email: 'priya@acme.test',
  email_verified: true,
  name: 'Priya Raman',
  picture: 'https://lh3.googleusercontent.com/a/abc',
}

describe('accepting a legitimate token', () => {
  test('a well-formed token yields the profile', () => {
    const profile = verifyIdTokenClaims(idToken(validClaims), NONCE)
    assert.equal(profile.sub, '1029384756')
    assert.equal(profile.email, 'priya@acme.test')
    assert.equal(profile.email_verified, true)
    assert.equal(profile.name, 'Priya Raman')
  })

  test('both accepted issuer spellings work', () => {
    for (const iss of ['accounts.google.com', 'https://accounts.google.com']) {
      assert.doesNotThrow(() => verifyIdTokenClaims(idToken({ ...validClaims, iss }), NONCE))
    }
  })

  test('an audience array containing our client id is accepted', () => {
    const claims = { ...validClaims, aud: ['someone-else', 'our-client-id.apps.googleusercontent.com'] }
    assert.doesNotThrow(() => verifyIdTokenClaims(idToken(claims), NONCE))
  })

  test('email_verified as the string "true" is honoured', () => {
    // Some OIDC providers stringify booleans; treating "true" as false would refuse
    // a legitimate account.
    const profile = verifyIdTokenClaims(idToken({ ...validClaims, email_verified: 'true' }), NONCE)
    assert.equal(profile.email_verified, true)
  })
})

describe('refusing a token that is not for us', () => {
  test('a token minted for another application is refused', () => {
    // The single most important check: without it, any Google app could mint a token
    // that signs its holder into this one.
    assert.throws(
      () => verifyIdTokenClaims(idToken({ ...validClaims, aud: 'attacker-client-id' }), NONCE),
      /issued for another application/,
    )
  })

  test('a foreign issuer is refused', () => {
    assert.throws(
      () => verifyIdTokenClaims(idToken({ ...validClaims, iss: 'https://evil.example' }), NONCE),
      /not issued by Google/,
    )
  })

  test('an expired token is refused', () => {
    assert.throws(() => verifyIdTokenClaims(idToken({ ...validClaims, exp: PAST }), NONCE), /expired/)
  })

  test('a missing expiry is refused, not treated as never-expiring', () => {
    const { exp: _drop, ...noExp } = validClaims
    assert.throws(() => verifyIdTokenClaims(idToken(noExp), NONCE), /expired/)
  })

  test('a mismatched nonce is refused — this blocks replay into our session', () => {
    assert.throws(
      () => verifyIdTokenClaims(idToken({ ...validClaims, nonce: 'someone-elses-nonce' }), NONCE),
      /could not be verified/,
    )
  })

  test('a missing nonce is refused', () => {
    const { nonce: _drop, ...noNonce } = validClaims
    assert.throws(() => verifyIdTokenClaims(idToken(noNonce), NONCE), /could not be verified/)
  })

  test('a token with no subject is refused', () => {
    const { sub: _drop, ...noSub } = validClaims
    assert.throws(() => verifyIdTokenClaims(idToken(noSub), NONCE), /account identifier/)
  })
})

describe('refusing malformed input', () => {
  test('a token without three parts is refused', () => {
    for (const bad of ['', 'a', 'a.b', 'a.b.c.d']) {
      assert.throws(() => verifyIdTokenClaims(bad, NONCE), /malformed/i, `accepted ${JSON.stringify(bad)}`)
    }
  })

  test('an unparseable payload is refused rather than throwing a raw SyntaxError', () => {
    const bad = `${Buffer.from('{}').toString('base64url')}.${Buffer.from('not json').toString('base64url')}.sig`
    assert.throws(() => verifyIdTokenClaims(bad, NONCE), /unreadable/)
  })
})

describe('availability', () => {
  test('configured when both client id and secret are present', () => {
    assert.equal(googleConfigured(), true)
  })
})

describe('what the verifier deliberately does NOT decide', () => {
  test('it reports email_verified rather than enforcing it', () => {
    /*
     * The refusal lives in `completeGoogleSignIn`, not here, because the check needs
     * the *userinfo* response too — the id_token is only one of the two sources
     * (SPEC-007 §3.2). This test pins that separation: the verifier must pass the
     * flag through untouched rather than silently accepting an unverified address.
     */
    const profile = verifyIdTokenClaims(idToken({ ...validClaims, email_verified: false }), NONCE)
    assert.equal(profile.email_verified, false)
  })
})
