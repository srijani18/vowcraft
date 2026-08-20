import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { record } from '@/lib/audit'
import { db } from '@/lib/db'
import { env } from '@/lib/env'
import { AppError } from '@/lib/errors'
import { logger } from '@/lib/logger'
import { buildSession, sessionsAvailable, type SessionPayload } from '@/lib/session'
import { sendWelcome } from './service'

/**
 * Sign in with Google — SPEC-007 §3.
 *
 * Authentication, not authorisation: this establishes *who someone is*, whereas the
 * adapters in `integrations/` obtain permission to act on an account that already
 * exists. Same OAuth app and the same `OAuthState` table, different scopes and a
 * different destination for the result.
 */

const AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
const TOKEN = 'https://oauth2.googleapis.com/token'
const USERINFO = 'https://openidconnect.googleapis.com/v1/userinfo'
const SCOPES = ['openid', 'email', 'profile']
const STATE_TTL_MS = 10 * 60_000
const PROVIDER = 'google'

export function googleConfigured(): boolean {
  const e = env()
  return Boolean(e.GOOGLE_CLIENT_ID && e.GOOGLE_CLIENT_SECRET)
}

function redirectUri(): string {
  return `${env().APP_URL.replace(/\/+$/, '')}/api/auth/google/callback`
}

// ─────────────────────────────────────────────────────────────────── start ──

export async function startGoogleSignIn(nextPath?: string): Promise<string> {
  if (!googleConfigured()) {
    throw new AppError(
      503,
      'google_not_configured',
      'Google sign-in is not configured on this deployment.',
    )
  }
  if (!sessionsAvailable()) {
    throw new AppError(503, 'sessions_unavailable', 'APP_ENCRYPTION_KEY is not configured.')
  }

  const state = `st_${randomUUID().replace(/-/g, '')}`
  const codeVerifier = randomBytes(32).toString('base64url')
  const codeChallenge = createHash('sha256').update(codeVerifier).digest('base64url')
  const nonce = randomBytes(16).toString('base64url')

  await db.oAuthState.create({
    data: {
      state,
      purpose: 'signin',
      // No user yet — that is the whole point of this flow.
      userId: null,
      provider: PROVIDER,
      codeVerifier,
      nonce,
      redirectUri: redirectUri(),
      // Carried server-side so it cannot be swapped mid-flight for an off-site URL.
      nextPath: nextPath && nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : null,
      expiresAt: new Date(Date.now() + STATE_TTL_MS),
    },
  })

  const params = new URLSearchParams({
    client_id: env().GOOGLE_CLIENT_ID!,
    redirect_uri: redirectUri(),
    response_type: 'code',
    scope: SCOPES.join(' '),
    state,
    nonce,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
    // No refresh token needed: this is a one-shot identity check, not ongoing access.
    access_type: 'online',
    prompt: 'select_account',
  })

  return `${AUTH}?${params.toString()}`
}

// ──────────────────────────────────────────────────────────────── callback ──

interface TokenResponse {
  access_token?: string
  id_token?: string
  error?: string
  error_description?: string
}

export interface GoogleProfile {
  sub: string
  email?: string
  email_verified?: boolean
  name?: string
  picture?: string
}

/**
 * Decodes the id_token payload and checks the claims that matter.
 *
 * The **signature is not verified**, deliberately: the token arrived in the response
 * body of a server-to-server POST to Google's token endpoint over TLS, which is the
 * case Google's own documentation identifies as not requiring validation. The claim
 * checks below still run, because a signature would only prove the token is genuine —
 * not that it was issued *for us*. `aud` is the check that establishes that, and
 * omitting it would accept an id_token minted for a different application entirely.
 */
export function verifyIdTokenClaims(idToken: string, expectedNonce: string): GoogleProfile {
  const parts = idToken.split('.')
  if (parts.length !== 3) {
    throw new AppError(400, 'google_bad_id_token', 'Google returned a malformed identity token.')
  }

  let claims: Record<string, unknown>
  try {
    claims = JSON.parse(Buffer.from(parts[1]!, 'base64url').toString('utf8'))
  } catch {
    throw new AppError(400, 'google_bad_id_token', 'Google returned an unreadable identity token.')
  }

  const iss = String(claims.iss ?? '')
  if (iss !== 'accounts.google.com' && iss !== 'https://accounts.google.com') {
    throw new AppError(400, 'google_bad_issuer', 'That identity token was not issued by Google.')
  }

  const aud = claims.aud
  const audiences = Array.isArray(aud) ? aud.map(String) : [String(aud ?? '')]
  if (!audiences.includes(env().GOOGLE_CLIENT_ID!)) {
    throw new AppError(400, 'google_bad_audience', 'That identity token was issued for another application.')
  }

  const exp = Number(claims.exp ?? 0)
  if (!Number.isFinite(exp) || exp * 1000 <= Date.now()) {
    throw new AppError(400, 'google_expired_id_token', 'That sign-in attempt expired. Try again.')
  }

  // Binds this token to the redirect we initiated, so a token captured from another
  // session cannot be replayed into ours.
  if (String(claims.nonce ?? '') !== expectedNonce) {
    throw new AppError(400, 'google_nonce_mismatch', 'That sign-in attempt could not be verified. Try again.')
  }

  const sub = String(claims.sub ?? '')
  if (!sub) throw new AppError(400, 'google_no_subject', 'Google did not return an account identifier.')

  return {
    sub,
    email: typeof claims.email === 'string' ? claims.email : undefined,
    email_verified: claims.email_verified === true || claims.email_verified === 'true',
    name: typeof claims.name === 'string' ? claims.name : undefined,
    picture: typeof claims.picture === 'string' ? claims.picture : undefined,
  }
}

async function fetchProfile(accessToken: string): Promise<GoogleProfile> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 12_000)
  const response = await fetch(USERINFO, {
    headers: { authorization: `Bearer ${accessToken}` },
    signal: controller.signal,
  }).finally(() => clearTimeout(timer))

  if (!response.ok) {
    throw new AppError(502, 'google_userinfo_failed', 'Could not read your Google profile. Try again.')
  }
  return (await response.json()) as GoogleProfile
}

export interface GoogleSignInResult {
  session: SessionPayload
  user: { id: string; email: string; name: string | null }
  nextPath: string | null
  /** 'created' | 'linked' | 'returning' — used for the audit trail and the toast. */
  outcome: 'created' | 'linked' | 'returning'
}

export async function completeGoogleSignIn(
  code: string,
  state: string,
  requestId: string,
): Promise<GoogleSignInResult> {
  const log = logger.child({ requestId, provider: PROVIDER })

  // ── burn the state, single-use
  const row = await db.oAuthState.findUnique({ where: { state } })
  if (!row || row.provider !== PROVIDER || row.purpose !== 'signin') {
    throw new AppError(400, 'invalid_state', 'That sign-in link is not valid. Start again.')
  }
  if (row.consumedAt) {
    throw new AppError(400, 'state_replayed', 'That sign-in link was already used. Start again.')
  }
  if (row.expiresAt.getTime() < Date.now()) {
    throw new AppError(400, 'state_expired', 'That sign-in attempt expired. Start again.')
  }
  const burned = await db.oAuthState.updateMany({
    where: { state, consumedAt: null },
    data: { consumedAt: new Date() },
  })
  if (burned.count === 0) {
    throw new AppError(400, 'state_replayed', 'That sign-in link was already used. Start again.')
  }

  // ── exchange the code
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 12_000)
  const tokenResponse = await fetch(TOKEN, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      code,
      client_id: env().GOOGLE_CLIENT_ID!,
      client_secret: env().GOOGLE_CLIENT_SECRET!,
      redirect_uri: row.redirectUri,
      grant_type: 'authorization_code',
      code_verifier: row.codeVerifier,
    }),
    signal: controller.signal,
  })
    .finally(() => clearTimeout(timer))
    .catch(() => {
      throw new AppError(502, 'google_unreachable', 'Could not reach Google. Try again.')
    })

  const tokens = (await tokenResponse.json().catch(() => ({}))) as TokenResponse
  if (!tokenResponse.ok || !tokens.access_token || !tokens.id_token) {
    log.warn('auth.google_exchange_failed', { status: tokenResponse.status, error: tokens.error })
    throw new AppError(502, 'google_exchange_failed', 'Google would not complete the sign-in. Try again.')
  }

  // ── two independent checks on the identity (SPEC-007 §3.2)
  const claimed = verifyIdTokenClaims(tokens.id_token, row.nonce ?? '')
  const profile = await fetchProfile(tokens.access_token)

  if (profile.sub !== claimed.sub) {
    // Should be impossible; if it happens, something is very wrong upstream.
    throw new AppError(400, 'google_subject_mismatch', 'Google returned inconsistent identity data.')
  }

  const email = (profile.email ?? claimed.email ?? '').trim().toLowerCase()
  const verified = profile.email_verified === true || claimed.email_verified === true

  if (!email) {
    throw new AppError(400, 'google_no_email', 'Your Google account did not share an email address.')
  }

  /*
   * The single most important line in this feature. Linking an unverified Google
   * email to an existing local account would let anyone who can create a Google
   * account claiming that address take it over.
   */
  if (!verified) {
    log.warn('auth.google_unverified_email', { sub: profile.sub })
    throw new AppError(
      403,
      'google_email_unverified',
      'That Google account has not verified its email address, so it cannot be used to sign in.',
    )
  }

  const name = profile.name ?? claimed.name ?? null
  const avatarUrl = profile.picture ?? claimed.picture ?? null
  const now = new Date()

  // ── find or create, keyed on `sub` and never on the email
  const existingIdentity = await db.authIdentity.findUnique({
    where: { provider_providerAccountId: { provider: PROVIDER, providerAccountId: profile.sub } },
    select: { id: true, userId: true },
  })

  let outcome: GoogleSignInResult['outcome']
  let userId: string

  if (existingIdentity) {
    outcome = 'returning'
    userId = existingIdentity.userId
    await db.authIdentity.update({
      where: { id: existingIdentity.id },
      data: { lastLoginAt: now, email, name, avatarUrl },
    })
  } else {
    const byEmail = await db.user.findUnique({ where: { email }, select: { id: true, name: true } })

    if (byEmail) {
      outcome = 'linked'
      userId = byEmail.id
      await db.$transaction(async (tx) => {
        await tx.authIdentity.create({
          data: {
            userId: byEmail.id,
            provider: PROVIDER,
            providerAccountId: profile.sub,
            email,
            name,
            avatarUrl,
            lastLoginAt: now,
          },
        })
        // Fill in a name only if the account has none — never overwrite one the
        // person chose themselves.
        if (!byEmail.name && name) {
          await tx.user.update({ where: { id: byEmail.id }, data: { name } })
        }
        await record(tx, {
          event: 'auth.identity_linked',
          actorId: byEmail.id,
          requestId,
          metadata: { provider: PROVIDER, email },
        })
      })
    } else {
      outcome = 'created'
      const created = await db.$transaction(async (tx) => {
        // Same shape as local sign-up: settings and a first roster entry exist from
        // the first request, so the rule engine never falls back to defaults.
        const user = await tx.user.create({
          data: {
            email,
            name,
            image: avatarUrl,
            // No password. `POST /api/profile/password` will set a first one without
            // requiring a current one (SPEC-005 §3), so a user can end up with both.
            passwordHash: null,
            settings: { create: {} },
            teamMembers: { create: { name: name ?? email, email, role: 'Owner' } },
            identities: {
              create: {
                provider: PROVIDER,
                providerAccountId: profile.sub,
                email,
                name,
                avatarUrl,
                lastLoginAt: now,
              },
            },
          },
          select: { id: true },
        })
        await record(tx, {
          event: 'auth.identity_created',
          actorId: user.id,
          requestId,
          metadata: { provider: PROVIDER, email },
        })
        return user
      })
      userId = created.id
    }
  }

  const user = await db.user.findUniqueOrThrow({
    where: { id: userId },
    select: { id: true, email: true, name: true, passwordUpdatedAt: true },
  })

  await record(db, {
    event: 'auth.signed_in',
    actorId: user.id,
    requestId,
    metadata: { provider: PROVIDER, outcome },
  })
  log.info('auth.google_signed_in', { outcome, userId: user.id })

  // Only for a genuinely new account — a returning user or a newly linked identity
  // does not need welcoming again.
  if (outcome === 'created') {
    await sendWelcome(user.id, user.email, user.name, 'google', requestId)
  }

  return {
    session: buildSession(user.id, user.passwordUpdatedAt),
    user: { id: user.id, email: user.email, name: user.name },
    nextPath: row.nextPath,
    outcome,
  }
}
