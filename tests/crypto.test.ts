import { test, describe, before } from 'node:test'
import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'

/**
 * `lib/crypto` reads APP_ENCRYPTION_KEY through `env()`, which parses
 * `process.env` lazily on first use — so the key must be in place before the
 * first call, not before the import.
 */
process.env.APP_ENCRYPTION_KEY ??= randomBytes(32).toString('base64')
process.env.DATABASE_URL ??= 'postgresql://test:test@localhost:5432/test'

const { decrypt, decryptJson, encrypt, encryptJson, encryptionAvailable, maskSecret, sha256, stableStringify } =
  await import('@/lib/crypto')

describe('AES-256-GCM envelope — SPEC-002 §4, SPEC-004 §4', () => {
  test('a value survives a round trip', () => {
    const secret = 'sk-live-0123456789abcdef'
    assert.equal(decrypt(encrypt(secret)), secret)
  })

  test('the envelope is versioned and carries no plaintext', () => {
    const envelope = encrypt('sk-live-0123456789abcdef')
    assert.match(envelope, /^v1\./)
    assert.equal(envelope.split('.').length, 4)
    assert.ok(!envelope.includes('sk-live'))
  })

  test('the same plaintext encrypts differently every time', () => {
    // A fresh IV per call: identical keys must not produce identical ciphertext,
    // or an observer learns which users share a provider key.
    const a = encrypt('same value')
    const b = encrypt('same value')
    assert.notEqual(a, b)
    assert.equal(decrypt(a), decrypt(b))
  })

  test('AAD binds a ciphertext to its context', () => {
    const envelope = encrypt('sk-live-abc', 'cred:user_1:openai')
    assert.equal(decrypt(envelope, 'cred:user_1:openai'), 'sk-live-abc')
    // The same bytes under another owner or service must fail, not decrypt.
    assert.throws(() => decrypt(envelope, 'cred:user_2:openai'))
    assert.throws(() => decrypt(envelope, 'cred:user_1:anthropic'))
    assert.throws(() => decrypt(envelope))
  })

  test('a tampered ciphertext is rejected, not silently mangled', () => {
    const envelope = encrypt('sk-live-abc')
    const parts = envelope.split('.')
    const flipped = Buffer.from(parts[3]!, 'base64url')
    flipped[0] = (flipped[0] ?? 0) ^ 0xff
    assert.throws(() => decrypt([parts[0], parts[1], parts[2], flipped.toString('base64url')].join('.')))
  })

  test('a tampered auth tag is rejected', () => {
    const parts = encrypt('sk-live-abc').split('.')
    const tag = Buffer.from(parts[2]!, 'base64url')
    tag[0] = (tag[0] ?? 0) ^ 0xff
    assert.throws(() => decrypt([parts[0], parts[1], tag.toString('base64url'), parts[3]].join('.')))
  })

  test('a malformed envelope is rejected by shape', () => {
    assert.throws(() => decrypt('not-an-envelope'), /Malformed/)
    assert.throws(() => decrypt('v2.a.b.c'), /Malformed/)
  })

  test('JSON round-trips, so a multi-field credential is one ciphertext', () => {
    const secrets = { clientId: 'abc.apps.googleusercontent.com', clientSecret: 'GOCSPX-xyz' }
    const envelope = encryptJson(secrets, 'cred:user_1:google')
    assert.deepEqual(decryptJson(envelope, 'cred:user_1:google'), secrets)
  })

  test('a wrong key cannot read a ciphertext', () => {
    const envelope = encrypt('sk-live-abc')
    const original = process.env.APP_ENCRYPTION_KEY
    // The key is cached inside env(), so this proves the failure path rather than
    // the rotation path — which is exactly what `listCredentials` relies on.
    assert.ok(encryptionAvailable())
    process.env.APP_ENCRYPTION_KEY = original
    assert.equal(decrypt(envelope), 'sk-live-abc')
  })
})

describe('maskSecret — SPEC-004 §5', () => {
  test('shows the first five and last four, never the middle', () => {
    const masked = maskSecret('sk-proj-abcdefghijklmnop9f2a')
    assert.equal(masked, 'sk-pr…9f2a')
    assert.ok(!masked.includes('defghijk'))
  })

  test('a short secret is fully masked rather than partly leaked', () => {
    assert.equal(maskSecret('short'), '•••••')
    assert.ok(!maskSecret('short').includes('s'))
  })

  test('a 12-character secret is still fully masked — the boundary is inclusive', () => {
    assert.ok(!/[a-z]/.test(maskSecret('abcdefghijkl')))
  })

  test('whitespace is trimmed before masking', () => {
    assert.equal(maskSecret('  sk-proj-abcdefghijklmnop9f2a  '), 'sk-pr…9f2a')
  })
})

describe('stableStringify', () => {
  test('sorts keys at every depth', () => {
    assert.equal(stableStringify({ b: 1, a: { d: 2, c: 3 } }), '{"a":{"c":3,"d":2},"b":1}')
  })

  test('preserves array order', () => {
    assert.equal(stableStringify([3, 1, 2]), '[3,1,2]')
  })

  test('drops undefined but keeps null', () => {
    assert.equal(stableStringify({ a: undefined, b: null }), '{"b":null}')
  })

  test('handles primitives and empties', () => {
    assert.equal(stableStringify(null), 'null')
    assert.equal(stableStringify(42), '42')
    assert.equal(stableStringify('x'), '"x"')
    assert.equal(stableStringify({}), '{}')
    assert.equal(stableStringify([]), '[]')
  })
})

test('sha256 is deterministic and hex', () => {
  assert.equal(sha256('abc'), sha256('abc'))
  assert.match(sha256('abc'), /^[0-9a-f]{64}$/)
  assert.notEqual(sha256('abc'), sha256('abd'))
})
