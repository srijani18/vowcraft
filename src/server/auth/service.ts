import { z } from 'zod'
import { record } from '@/lib/audit'
import { db } from '@/lib/db'
import { AppError, conflict, unprocessable } from '@/lib/errors'
import { hashPassword, validatePassword, verifyPassword } from '@/lib/password'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'
import { buildSession, sessionsAvailable, type SessionPayload } from '@/lib/session'
import { sendMail, welcomeEmail } from '../email/mailer'

/**
 * Sign-up and sign-in — SPEC-006 §4.
 *
 * Both return a session payload for the route handler to set as a cookie; neither
 * touches cookies itself, so the same functions are callable from a test or a CLI
 * without a request context.
 */

export const signupSchema = z.object({
  name: z.string().trim().min(2, 'Enter your name').max(120),
  email: z.string().trim().toLowerCase().email('That does not look like an email address'),
  password: z.string().min(1, 'Choose a password'),
})

export const loginSchema = z.object({
  email: z.string().trim().toLowerCase().email('That does not look like an email address'),
  password: z.string().min(1, 'Enter your password'),
})

export interface AuthResult {
  session: SessionPayload
  user: { id: string; email: string; name: string | null }
}

function requireSessions() {
  if (!sessionsAvailable()) {
    throw new AppError(
      503,
      'sessions_unavailable',
      'APP_ENCRYPTION_KEY is not configured, so sessions cannot be signed. Generate one with: openssl rand -base64 32',
    )
  }
}

export async function signup(
  input: z.infer<typeof signupSchema>,
  requestId: string,
): Promise<AuthResult> {
  requireSessions()

  const problem = validatePassword(input.password, { email: input.email, name: input.name })
  if (problem) throw unprocessable(problem.code, problem.message)

  const existing = await db.user.findUnique({ where: { email: input.email }, select: { id: true } })
  if (existing) {
    // Sign-up is the one place account existence is unavoidably disclosed — the
    // alternative is silently doing nothing and leaving someone stuck. Login stays
    // deliberately vague for exactly the opposite reason.
    throw conflict('account_exists', 'An account already exists for that email address.')
  }

  const passwordHash = await hashPassword(input.password)
  const now = new Date()

  const user = await db.$transaction(async (tx) => {
    const created = await tx.user.create({
      data: {
        email: input.email,
        name: input.name,
        passwordHash,
        passwordUpdatedAt: now,
        // Settings exist from the first request, so the guardrail engine always has
        // a real envelope to read rather than silently falling back to defaults.
        settings: { create: {} },
        // The owner is their own first team member, which makes VAL_OWNER_KNOWN
        // meaningful immediately instead of warning on every item.
        teamMembers: { create: { name: input.name, email: input.email, role: 'Owner' } },
      },
      select: { id: true, email: true, name: true, passwordUpdatedAt: true },
    })

    await record(tx, {
      event: 'auth.signed_up',
      actorId: created.id,
      requestId,
      metadata: { email: created.email },
    })

    return created
  })

  await sendWelcome(user.id, user.email, user.name, 'password', requestId)

  return {
    session: buildSession(user.id, user.passwordUpdatedAt),
    user: { id: user.id, email: user.email, name: user.name },
  }
}

/**
 * Welcome email, fire-and-forget — SPEC-007 §4.
 *
 * Deliberately not awaited into the response and deliberately not able to fail the
 * registration: an account that exists but whose welcome email bounced is a minor
 * annoyance, whereas a sign-up that 500s because an email provider was down loses the
 * user entirely. Failures are logged, not raised.
 */
export async function sendWelcome(
  userId: string,
  email: string,
  name: string | null,
  method: 'password' | 'google',
  requestId: string,
): Promise<void> {
  try {
    const message = welcomeEmail(name, env().APP_URL.replace(/\/+$/, ''), method)
    const result = await sendMail({ ...message, to: email }, { userId, requestId })
    logger.info('auth.welcome_email', { userId, method, transport: result.transport, delivered: result.delivered })
  } catch (err) {
    logger.error('auth.welcome_email_failed', { userId, method, err })
  }
}

export async function login(
  input: z.infer<typeof loginSchema>,
  requestId: string,
): Promise<AuthResult> {
  requireSessions()

  const user = await db.user.findUnique({
    where: { email: input.email },
    select: {
      id: true,
      email: true,
      name: true,
      passwordHash: true,
      passwordUpdatedAt: true,
    },
  })

  /*
   * One message for every failure mode — unknown address, no password set, wrong
   * password. Differentiating them turns this endpoint into an account enumerator,
   * and the dummy hash below evens out the timing for the same reason.
   */
  const genericFailure = new AppError(
    401,
    'invalid_credentials',
    'That email and password combination is not recognised.',
  )

  if (!user?.passwordHash) {
    await hashPassword(input.password).catch(() => undefined)
    throw genericFailure
  }

  if (!(await verifyPassword(input.password, user.passwordHash))) {
    await record(db, {
      event: 'auth.sign_in_failed',
      actorId: user.id,
      requestId,
      metadata: { reason: 'bad_password' },
    })
    throw genericFailure
  }

  await record(db, { event: 'auth.signed_in', actorId: user.id, requestId })

  return {
    session: buildSession(user.id, user.passwordUpdatedAt),
    user: { id: user.id, email: user.email, name: user.name },
  }
}
