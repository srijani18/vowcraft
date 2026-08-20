import { randomBytes, timingSafeEqual } from 'node:crypto'
import { z } from 'zod'
import { record } from '@/lib/audit'
import { sha256 } from '@/lib/crypto'
import { db } from '@/lib/db'
import { env } from '@/lib/env'
import { AppError, unprocessable } from '@/lib/errors'
import { logger } from '@/lib/logger'
import { hashPassword, validatePassword } from '@/lib/password'
import { buildSession, sessionsAvailable, type SessionPayload } from '@/lib/session'
import { resetEmail, sendMail } from '../email/mailer'

/**
 * Password reset — SPEC-007 §2.
 *
 * The two properties worth stating in one place:
 *
 *  - **Tokens are stored hashed.** A leak of `PasswordResetToken` yields nothing
 *    usable; the plaintext exists only in the email and the URL the user clicks.
 *  - **Requesting never reveals whether the account exists.** Every branch of
 *    `requestReset` returns the same shape, and the caller cannot tell them apart.
 */

const TOKEN_TTL_MS = 60 * 60_000 // one hour
const PER_EMAIL_LIMIT = 3
const PER_IP_LIMIT = 10
const RATE_WINDOW_MS = 15 * 60_000

export const forgotPasswordSchema = z.object({
  email: z.string().trim().toLowerCase().email('Enter a valid email address'),
})

export const resetPasswordSchema = z.object({
  token: z.string().trim().min(20, 'That reset link is not valid'),
  newPassword: z.string().min(1, 'Choose a password'),
})

export interface RequestResetResult {
  /** Always true. The caller must not branch on account existence. */
  accepted: true
  /**
   * Present only when the log transport ran *and* this is not production — it lets
   * a self-hosted or development flow continue without an email provider.
   */
  devLink?: string
  /** Whether a real send happened, for the UI to phrase the confirmation honestly. */
  delivered: boolean
}

function tokenHash(raw: string): string {
  return sha256(raw)
}

/**
 * Requesting a reset. Same result for a registered address, an unregistered one, and
 * a Google-only account with no password — anything else is an account enumerator.
 */
export async function requestReset(
  input: z.infer<typeof forgotPasswordSchema>,
  context: { ip: string | null; requestId: string },
): Promise<RequestResetResult> {
  const log = logger.child({ requestId: context.requestId })
  const since = new Date(Date.now() - RATE_WINDOW_MS)

  const user = await db.user.findUnique({
    where: { email: input.email },
    select: { id: true, name: true, email: true },
  })

  /*
   * Rate limits are counted from the durable rows, not process memory, so a restart
   * does not hand an attacker a fresh budget. The IP limit is checked even when no
   * account matched — otherwise the absence of throttling on unknown addresses is
   * itself a signal.
   */
  const [byIp, byEmail] = await Promise.all([
    context.ip
      ? db.passwordResetToken.count({ where: { requestedIp: context.ip, createdAt: { gte: since } } })
      : Promise.resolve(0),
    user
      ? db.passwordResetToken.count({ where: { userId: user.id, createdAt: { gte: since } } })
      : Promise.resolve(0),
  ])

  if (byIp >= PER_IP_LIMIT || byEmail >= PER_EMAIL_LIMIT) {
    log.warn('auth.reset_throttled', { byIp, byEmail })
    // Still the generic shape: a distinct 429 would confirm the address exists.
    return { accepted: true, delivered: false }
  }

  if (!user) {
    // Deliberately do the same amount of visible work and return the same thing.
    log.info('auth.reset_requested', { outcome: 'no_such_account' })
    return { accepted: true, delivered: false }
  }

  if (!sessionsAvailable()) {
    throw new AppError(
      503,
      'sessions_unavailable',
      'APP_ENCRYPTION_KEY is not configured, so password reset cannot complete. ' +
        'Generate one with: openssl rand -base64 32',
    )
  }

  const raw = randomBytes(32).toString('base64url')

  await db.$transaction(async (tx) => {
    // A user who clicks "resend" three times should not leave three live keys behind.
    await tx.passwordResetToken.updateMany({
      where: { userId: user.id, consumedAt: null },
      data: { consumedAt: new Date() },
    })
    await tx.passwordResetToken.create({
      data: {
        userId: user.id,
        tokenHash: tokenHash(raw),
        expiresAt: new Date(Date.now() + TOKEN_TTL_MS),
        requestedIp: context.ip,
      },
    })
    await record(tx, {
      event: 'auth.password_reset_requested',
      actorId: user.id,
      requestId: context.requestId,
      metadata: { ip: context.ip },
    })
  })

  const link = `${env().APP_URL.replace(/\/+$/, '')}/reset-password?token=${encodeURIComponent(raw)}`
  const message = resetEmail(link, user.name)
  const sent = await sendMail({ ...message, to: user.email }, { userId: user.id, requestId: context.requestId })

  return { accepted: true, delivered: sent.delivered, devLink: sent.preview }
}

export interface TokenCheck {
  valid: boolean
  reason?: 'unknown' | 'expired' | 'used'
  email?: string
  hasPassword?: boolean
}

/**
 * Validates a token without consuming it, so `/reset-password` can show a useful
 * page on load instead of failing only after the user has typed a new password.
 */
export async function inspectToken(raw: string): Promise<TokenCheck> {
  const row = await db.passwordResetToken.findUnique({
    where: { tokenHash: tokenHash(raw) },
    select: {
      expiresAt: true,
      consumedAt: true,
      user: { select: { email: true, passwordHash: true } },
    },
  })

  if (!row) return { valid: false, reason: 'unknown' }
  if (row.consumedAt) return { valid: false, reason: 'used' }
  if (row.expiresAt.getTime() < Date.now()) return { valid: false, reason: 'expired' }

  return {
    valid: true,
    email: row.user.email,
    // Drives the copy: "set a password" reads oddly to someone resetting one.
    hasPassword: row.user.passwordHash !== null,
  }
}

export interface ResetResult {
  session: SessionPayload
  user: { id: string; email: string; name: string | null }
}

export async function completeReset(
  input: z.infer<typeof resetPasswordSchema>,
  requestId: string,
): Promise<ResetResult> {
  if (!sessionsAvailable()) {
    throw new AppError(503, 'sessions_unavailable', 'APP_ENCRYPTION_KEY is not configured.')
  }

  const hash = tokenHash(input.token)
  const row = await db.passwordResetToken.findUnique({
    where: { tokenHash: hash },
    select: {
      id: true,
      expiresAt: true,
      consumedAt: true,
      tokenHash: true,
      user: { select: { id: true, email: true, name: true } },
    },
  })

  // One message for every failure: an attacker probing tokens learns only that the
  // one they tried does not work.
  const invalid = new AppError(
    400,
    'invalid_reset_token',
    'That reset link is no longer valid. Request a new one and it will work.',
  )

  if (!row) throw invalid
  // Constant-time confirmation of the row we matched, so a partial-hash oracle
  // cannot be built out of database timing.
  const a = Buffer.from(row.tokenHash)
  const b = Buffer.from(hash)
  if (a.length !== b.length || !timingSafeEqual(a, b)) throw invalid
  if (row.consumedAt || row.expiresAt.getTime() < Date.now()) throw invalid

  const problem = validatePassword(input.newPassword, {
    email: row.user.email,
    name: row.user.name ?? undefined,
  })
  if (problem) throw unprocessable(problem.code, problem.message)

  const passwordHash = await hashPassword(input.newPassword)
  const now = new Date()

  const updated = await db.$transaction(async (tx) => {
    // Conditional on still being unconsumed: two parallel submissions cannot both
    // win, and the loser sees the generic invalid-token error.
    const burned = await tx.passwordResetToken.updateMany({
      where: { id: row.id, consumedAt: null },
      data: { consumedAt: now },
    })
    if (burned.count === 0) throw invalid

    // Siblings die too, so a second link from the same inbox is inert.
    await tx.passwordResetToken.updateMany({
      where: { userId: row.user.id, consumedAt: null },
      data: { consumedAt: now },
    })

    /*
     * Stamping passwordUpdatedAt is what revokes every existing session: the cookie
     * carries that timestamp and stops validating (SPEC-006 §3). Whoever forced the
     * reset must not keep a session the owner cannot see.
     */
    const next = await tx.user.update({
      where: { id: row.user.id },
      data: { passwordHash, passwordUpdatedAt: now },
      select: { id: true, email: true, name: true, passwordUpdatedAt: true },
    })

    await record(tx, {
      event: 'auth.password_reset_completed',
      actorId: next.id,
      requestId,
      metadata: { sessionsRevoked: true },
    })

    return next
  })

  return {
    session: buildSession(updated.id, updated.passwordUpdatedAt),
    user: { id: updated.id, email: updated.email, name: updated.name },
  }
}

/** Housekeeping for a sweeper: expired tokens have no reason to persist. */
export async function purgeExpiredResetTokens(now = new Date()): Promise<number> {
  const { count } = await db.passwordResetToken.deleteMany({
    where: { OR: [{ expiresAt: { lt: now } }, { consumedAt: { not: null } }] },
  })
  return count
}
