import { randomBytes, scrypt as scryptCb, timingSafeEqual } from 'node:crypto'
import { promisify } from 'node:util'

/**
 * Password hashing — SPEC-005 §3.
 *
 * scrypt from `node:crypto` rather than bcrypt or argon2: it is memory-hard,
 * it is in the standard library, and it needs no native build step — which
 * matters because a native dependency that fails to compile on Alpine is a
 * deployment problem, not a security improvement.
 *
 * Stored format: `s1.<salt>.<hash>`, base64url. The `s1` prefix makes the
 * parameters upgradable per row: a future `s2` can raise the cost factor and
 * rehash on next successful login rather than invalidating every password.
 */

const scrypt = promisify(scryptCb) as (
  password: string,
  salt: Buffer,
  keylen: number,
  options: { N: number; r: number; p: number; maxmem: number },
) => Promise<Buffer>

// N=2^15 keeps a single hash around 100ms on modest hardware — slow enough to
// make offline cracking expensive, fast enough not to hold a request open.
const PARAMS = { N: 32_768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 } as const
const KEYLEN = 64
const VERSION = 's1'

/*
 * Policy lives in `password-rules.ts` so the login and sign-up forms can import it
 * without pulling `node:crypto` into the client bundle. Re-exported here so server
 * callers have one import.
 */
export { MIN_PASSWORD_LENGTH, validatePassword } from './password-rules'
export type { FieldError as PasswordProblem } from './password-rules'

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16)
  const hash = await scrypt(password.normalize('NFKC'), salt, KEYLEN, PARAMS)
  return `${VERSION}.${salt.toString('base64url')}.${hash.toString('base64url')}`
}

/**
 * Constant-time comparison. Returns false for a malformed or absent hash rather
 * than throwing, so a caller cannot distinguish "no password set" from "wrong
 * password" by the shape of the failure.
 */
export async function verifyPassword(password: string, stored: string | null): Promise<boolean> {
  if (!stored) return false
  const parts = stored.split('.')
  if (parts.length !== 3 || parts[0] !== VERSION) return false

  try {
    const salt = Buffer.from(parts[1]!, 'base64url')
    const expected = Buffer.from(parts[2]!, 'base64url')
    if (expected.length !== KEYLEN) return false
    const actual = await scrypt(password.normalize('NFKC'), salt, KEYLEN, PARAMS)
    return timingSafeEqual(actual, expected)
  } catch {
    return false
  }
}
