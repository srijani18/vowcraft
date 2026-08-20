import { createCipheriv, createDecipheriv, randomBytes, createHash } from 'node:crypto'
import { env } from './env'

/**
 * AES-256-GCM envelope used for BOTH OAuth tokens (SPEC-002 §4) and BYOK
 * credentials (SPEC-004 §4). One implementation, so there is one thing to audit.
 *
 * Format: `v1.<iv>.<tag>.<ciphertext>` — all base64url. The version prefix makes
 * key rotation decidable per row instead of requiring a big-bang re-encrypt.
 *
 * `aad` binds a ciphertext to its context (e.g. `userId:service`). A row copied
 * to another user fails authentication rather than quietly decrypting.
 */

const VERSION = 'v1'

function key(): Buffer {
  const raw = env().APP_ENCRYPTION_KEY
  if (!raw) {
    throw new Error(
      'APP_ENCRYPTION_KEY is not set. Secrets cannot be stored. Generate one with: openssl rand -base64 32',
    )
  }
  const buf = Buffer.from(raw, 'base64')
  if (buf.length !== 32) {
    throw new Error(`APP_ENCRYPTION_KEY must decode to 32 bytes, got ${buf.length}`)
  }
  return buf
}

export function encryptionAvailable(): boolean {
  try {
    key()
    return true
  } catch {
    return false
  }
}

const b64u = (b: Buffer) => b.toString('base64url')

export function encrypt(plaintext: string, aad?: string): string {
  const iv = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', key(), iv)
  if (aad) cipher.setAAD(Buffer.from(aad, 'utf8'))
  const ct = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()])
  return [VERSION, b64u(iv), b64u(cipher.getAuthTag()), b64u(ct)].join('.')
}

export function decrypt(envelope: string, aad?: string): string {
  const parts = envelope.split('.')
  if (parts.length !== 4 || parts[0] !== VERSION) {
    throw new Error('Malformed ciphertext envelope')
  }
  const [, ivB64, tagB64, ctB64] = parts as [string, string, string, string]
  const decipher = createDecipheriv('aes-256-gcm', key(), Buffer.from(ivB64, 'base64url'))
  if (aad) decipher.setAAD(Buffer.from(aad, 'utf8'))
  decipher.setAuthTag(Buffer.from(tagB64, 'base64url'))
  // GCM raises here on a wrong key, tampered ciphertext, or mismatched AAD.
  return Buffer.concat([
    decipher.update(Buffer.from(ctB64, 'base64url')),
    decipher.final(),
  ]).toString('utf8')
}

export function encryptJson(value: unknown, aad?: string): string {
  return encrypt(JSON.stringify(value), aad)
}

export function decryptJson<T>(envelope: string, aad?: string): T {
  return JSON.parse(decrypt(envelope, aad)) as T
}

/**
 * Masked preview safe to return over the API (SPEC-004 §5): first 5 and last 4
 * characters, never the middle. Short secrets are fully masked rather than
 * partially leaked.
 */
export function maskSecret(secret: string): string {
  const s = secret.trim()
  if (s.length <= 12) return '•'.repeat(Math.max(s.length, 4))
  return `${s.slice(0, 5)}…${s.slice(-4)}`
}

export function sha256(input: string): string {
  return createHash('sha256').update(input).digest('hex')
}

/** Deterministic JSON for idempotency keys — SPEC-002 §5 step 3. */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableStringify(v)}`).join(',')}}`
}
