import { test, describe } from 'node:test'
import assert from 'node:assert/strict'

process.env.JWT_SECRET ??= 'dev-jwt-secret-change-for-anything-beyond-local-use'
process.env.JWT_ALGORITHM ??= 'HS256'

const { mintApiAccessToken, verifyApiAccessTokenForTests } = await import('@/lib/api-token')

const USER = { id: 'clx0000000000000000000000', pwdEpochMs: 1755600000123 }

function decode(token: string): { header: Record<string, unknown>; payload: Record<string, unknown> } {
  const [headerB64, payloadB64] = token.split('.') as [string, string]
  return {
    header: JSON.parse(Buffer.from(headerB64, 'base64url').toString('utf8')),
    payload: JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8')),
  }
}

describe('web-minted API token — SPEC-015 §7', () => {
  test('has three dot-separated parts, HS256, JWT typ', () => {
    const token = mintApiAccessToken(USER)
    assert.equal(token.split('.').length, 3)
    const { header } = decode(token)
    assert.equal(header.alg, 'HS256')
    assert.equal(header.typ, 'JWT')
  })

  test('claims match apps/api/app/core/security.py::issue_token exactly', () => {
    const token = mintApiAccessToken(USER, 60)
    const { payload } = decode(token)
    assert.equal(payload.sub, USER.id)
    assert.equal(payload.typ, 'access')
    assert.equal(payload.pwd, USER.pwdEpochMs)
    assert.equal(typeof payload.iat, 'number')
    assert.equal(payload.exp, (payload.iat as number) + 60)
  })

  test('self-verifies against the shared secret', () => {
    assert.equal(verifyApiAccessTokenForTests(mintApiAccessToken(USER)), true)
  })

  test('a tampered payload fails verification', () => {
    const token = mintApiAccessToken(USER)
    const [header, , signature] = token.split('.')
    const forged = Buffer.from(JSON.stringify({ ...decode(token).payload, sub: 'attacker' })).toString(
      'base64url',
    )
    assert.equal(verifyApiAccessTokenForTests(`${header}.${forged}.${signature}`), false)
  })

  test('throws rather than signing with no secret configured', async () => {
    const saved = process.env.JWT_SECRET
    delete process.env.JWT_SECRET
    try {
      assert.throws(() => mintApiAccessToken(USER))
    } finally {
      process.env.JWT_SECRET = saved
    }
  })
})
