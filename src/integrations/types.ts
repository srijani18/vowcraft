import type { z } from 'zod'
import type { ActionType } from '@/domain/types'

/** Provider contract — SPEC-002 §3. Adapters are interchangeable behind this. */

export type ProviderId = 'google_calendar' | 'notion' | 'gmail' | 'slack' | 'sendgrid'

/**
 * How an adapter authenticates. The executor uses this to decide what to put in
 * `ExecutionContext.accessToken`: an OAuth access token refreshed from
 * `IntegrationAccount`, or a plain API key resolved from the credential vault.
 */
export type AuthKind = 'oauth' | 'api_key'
export type IntegrationMode = 'mock' | 'live'

export interface TokenSet {
  accessToken: string
  refreshToken?: string
  /** Absolute expiry; adapters convert `expires_in` before returning. */
  expiresAt?: Date
  scopes?: string[]
  externalAccountId?: string
  accountLabel?: string
}

export interface ExecutionContext {
  mode: IntegrationMode
  /** Absent in mock mode — adapters must not assume a token exists. */
  accessToken?: string
  userEmail: string
  timeZone: string
  requestId: string
  /** Forwarded to providers that honour it, e.g. Notion / Slack. */
  idempotencyKey: string
}

export interface ProviderResult<R = unknown> {
  externalId: string
  externalUrl?: string
  /** One sentence a human can read in the success toast and the audit log. */
  summary: string
  raw: R
  simulated: boolean
}

/** Field-level table shown in the confirmation modal before anything is sent. */
export interface ExecutionPreview {
  provider: ProviderId
  /** "Creates a calendar event for 3 people" — the consequence, in one line. */
  consequence: string
  fields: { label: string; value: string }[]
}

export class ProviderError extends Error {
  readonly retryable: boolean
  readonly code: string
  readonly status?: number
  readonly retryAfterMs?: number
  /**
   * True when the request may have taken effect despite the error — a timeout
   * after dispatch. Non-idempotent providers must not be retried in this state
   * (SPEC-002 §6): a missing email is recoverable, a duplicate is not.
   */
  readonly uncertain: boolean

  constructor(init: {
    code: string
    message: string
    retryable?: boolean
    status?: number
    retryAfterMs?: number
    uncertain?: boolean
  }) {
    super(init.message)
    this.name = 'ProviderError'
    this.code = init.code
    this.retryable = init.retryable ?? false
    this.status = init.status
    this.retryAfterMs = init.retryAfterMs
    this.uncertain = init.uncertain ?? false
  }
}

export interface IntegrationProvider<P = unknown, R = unknown> {
  id: ProviderId
  displayName: string
  capability: ActionType
  auth: AuthKind
  /** For `api_key` providers: the credential-vault service id holding the key. */
  credentialService?: string
  scopes: string[]
  /** False when the OAuth app itself is unconfigured (no client id/secret). */
  isConfigured(): boolean
  /** Mutating providers that must not be retried after an uncertain failure. */
  idempotent: boolean

  /**
   * The zod schema behind `validate`. Typed loosely on purpose: schemas using
   * `.default()` or `.coerce` have an input type that differs from their output,
   * so a `ZodType<P>` annotation would reject exactly the schemas we want. `P`
   * flows from `validate()`, which is the only place callers need it.
   */
  schema: z.ZodTypeAny
  validate(payload: unknown): P
  preview(payload: P, ctx: Pick<ExecutionContext, 'timeZone' | 'userEmail'>): ExecutionPreview

  authorizeUrl(state: string, redirectUri: string, codeChallenge: string): string
  exchangeCode(code: string, redirectUri: string, codeVerifier: string): Promise<TokenSet>
  refresh(refreshToken: string): Promise<TokenSet>

  execute(payload: P, ctx: ExecutionContext): Promise<ProviderResult<R>>
}

/** Classifies an HTTP status per SPEC-002 §6. Only what can succeed later retries. */
export const RETRYABLE_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504])

export function isRetryableStatus(status: number): boolean {
  return RETRYABLE_STATUSES.has(status)
}
