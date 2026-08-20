import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { db } from '@/lib/db'
import { decrypt, encrypt } from '@/lib/crypto'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'
import { record } from '@/lib/audit'
import type { IntegrationProvider, TokenSet } from './types'
import { ProviderError } from './types'

/**
 * OAuth state, token storage, and refresh — SPEC-002 §4.
 *
 * Every token in the database is AES-256-GCM ciphertext bound to
 * `userId:provider` via AAD. Plaintext exists only inside this module's call
 * frames and is never logged, returned, or written to `executionResult`.
 */

const REFRESH_SKEW_MS = 120_000

// ───────────────────────────────────────────────────────── state + PKCE ──

export interface StartedAuthorization {
  url: string
  state: string
}

export async function startAuthorization(
  provider: IntegrationProvider,
  userId: string,
): Promise<StartedAuthorization> {
  if (!provider.isConfigured()) {
    throw new ProviderError({
      code: 'provider_unconfigured',
      message:
        `${provider.displayName} has no OAuth app configured. Add its client id and secret ` +
        `in Settings → API keys, or to the environment.`,
    })
  }

  const state = `st_${randomUUID().replace(/-/g, '')}`
  const codeVerifier = randomBytes(32).toString('base64url')
  const codeChallenge = createHash('sha256').update(codeVerifier).digest('base64url')
  const redirectUri = `${env().APP_URL}/api/integrations/${provider.id}/callback`

  await db.oAuthState.create({
    data: {
      state,
      userId,
      provider: provider.id,
      codeVerifier,
      redirectUri,
      purpose: 'integration',
      expiresAt: new Date(Date.now() + 10 * 60_000),
    },
  })

  return { url: provider.authorizeUrl(state, redirectUri, codeChallenge), state }
}

/**
 * Verifies and burns the state token. Single-use: `consumedAt` is set inside the
 * same conditional update, so a replayed callback finds nothing to consume.
 */
export async function consumeState(state: string, providerId: string) {
  const row = await db.oAuthState.findUnique({ where: { state } })
  if (!row || row.provider !== providerId) {
    throw new ProviderError({ code: 'invalid_state', message: 'This authorization link is not valid.' })
  }
  if (row.consumedAt) {
    throw new ProviderError({ code: 'state_replayed', message: 'This authorization link was already used.' })
  }
  if (row.expiresAt.getTime() < Date.now()) {
    throw new ProviderError({ code: 'state_expired', message: 'This authorization link has expired. Try again.' })
  }

  const burned = await db.oAuthState.updateMany({
    where: { state, consumedAt: null },
    data: { consumedAt: new Date() },
  })
  if (burned.count === 0) {
    throw new ProviderError({ code: 'state_replayed', message: 'This authorization link was already used.' })
  }

  return row
}

export async function completeAuthorization(
  provider: IntegrationProvider,
  code: string,
  state: string,
  requestId: string,
): Promise<void> {
  const row = await consumeState(state, provider.id)

  /*
   * `OAuthState.userId` became nullable when the table started serving Google
   * sign-in too, where there is no user yet (SPEC-007 §3.1). An *integration* grant
   * always has one, so a null here means a sign-in state reached the integration
   * callback — refuse it rather than guessing whose account to attach tokens to.
   */
  if (row.purpose !== 'integration' || !row.userId) {
    throw new ProviderError({
      code: 'state_purpose_mismatch',
      message: 'That authorization link was not issued for connecting an integration.',
    })
  }

  const tokens = await provider.exchangeCode(code, row.redirectUri, row.codeVerifier)
  await persistTokens(row.userId, provider.id, tokens)

  await record(db, {
    event: 'integration.connected',
    actorId: row.userId,
    requestId,
    metadata: { provider: provider.id, accountLabel: tokens.accountLabel ?? null },
  })
}

// ─────────────────────────────────────────────────────── token storage ──

function aad(userId: string, providerId: string): string {
  return `${userId}:${providerId}`
}

export async function persistTokens(
  userId: string,
  providerId: string,
  tokens: TokenSet,
): Promise<void> {
  const bind = aad(userId, providerId)
  const data = {
    accessTokenEnc: encrypt(tokens.accessToken, bind),
    // A refresh that omits refresh_token (Google does) must not erase the stored one.
    ...(tokens.refreshToken ? { refreshTokenEnc: encrypt(tokens.refreshToken, bind) } : {}),
    expiresAt: tokens.expiresAt ?? null,
    scopes: tokens.scopes ?? [],
    externalAccountId: tokens.externalAccountId ?? null,
    accountLabel: tokens.accountLabel ?? null,
    needsReauth: false,
  }

  await db.integrationAccount.upsert({
    where: { userId_provider: { userId, provider: providerId } },
    create: { userId, provider: providerId, ...data },
    update: data,
  })
}

/** Serialises concurrent refreshes per account within this process. */
const inflight = new Map<string, Promise<string>>()

/**
 * Returns a usable access token, refreshing first when it is within the skew
 * window. Throws `reauth_required` when the account needs the user back.
 */
export async function withFreshToken(
  provider: IntegrationProvider,
  userId: string,
): Promise<string> {
  const key = aad(userId, provider.id)
  const existing = inflight.get(key)
  if (existing) return existing

  const task = (async () => {
    const account = await db.integrationAccount.findUnique({
      where: { userId_provider: { userId, provider: provider.id } },
    })
    if (!account?.accessTokenEnc) {
      throw new ProviderError({
        code: 'not_connected',
        message: `${provider.displayName} is not connected for this account.`,
      })
    }
    if (account.needsReauth) {
      throw new ProviderError({
        code: 'reauth_required',
        message: `${provider.displayName} needs to be reconnected.`,
      })
    }

    const expiringSoon =
      account.expiresAt !== null && account.expiresAt.getTime() - Date.now() < REFRESH_SKEW_MS

    if (!expiringSoon) return decrypt(account.accessTokenEnc, key)

    if (!account.refreshTokenEnc) {
      await markNeedsReauth(userId, provider.id, 'expired_without_refresh_token')
      throw new ProviderError({
        code: 'reauth_required',
        message: `${provider.displayName} access expired and cannot be refreshed. Reconnect it.`,
      })
    }

    try {
      const refreshed = await provider.refresh(decrypt(account.refreshTokenEnc, key))
      await persistTokens(userId, provider.id, refreshed)
      logger.info('integration.token_refreshed', { provider: provider.id, userId })
      return refreshed.accessToken
    } catch (err) {
      await markNeedsReauth(userId, provider.id, (err as Error).message)
      throw new ProviderError({
        code: 'reauth_required',
        message: `Could not refresh ${provider.displayName} access. Reconnect it.`,
      })
    }
  })()

  inflight.set(key, task)
  try {
    return await task
  } finally {
    inflight.delete(key)
  }
}

export async function markNeedsReauth(
  userId: string,
  providerId: string,
  reason: string,
): Promise<void> {
  await db.integrationAccount.updateMany({
    where: { userId, provider: providerId },
    data: { needsReauth: true },
  })
  logger.warn('integration.reauth_required', { provider: providerId, userId, reason })
  await record(db, {
    event: 'integration.reauth_required',
    actorType: 'SYSTEM',
    actorId: userId,
    metadata: { provider: providerId, reason },
  })
}

/**
 * Forces one refresh + retry after a 401, then gives up (SPEC-002 §4). Two 401s
 * in a row mean the grant is gone, not that the token was stale.
 */
export async function onUnauthorized(
  provider: IntegrationProvider,
  userId: string,
): Promise<string> {
  const account = await db.integrationAccount.findUnique({
    where: { userId_provider: { userId, provider: provider.id } },
  })
  if (!account?.refreshTokenEnc) {
    await markNeedsReauth(userId, provider.id, 'unauthorized_without_refresh_token')
    throw new ProviderError({
      code: 'reauth_required',
      message: `${provider.displayName} needs to be reconnected.`,
    })
  }
  const refreshed = await provider.refresh(decrypt(account.refreshTokenEnc, aad(userId, provider.id)))
  await persistTokens(userId, provider.id, refreshed)
  return refreshed.accessToken
}
