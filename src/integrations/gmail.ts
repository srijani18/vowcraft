import { z } from 'zod'
import { env } from '@/lib/env'
import { mockId, providerFetch, simulateLatency } from './http'
import type { IntegrationProvider, ProviderResult, TokenSet } from './types'
import { ProviderError } from './types'

/**
 * Gmail adapter — satisfies EMAIL actions.
 *
 * `sendMode: 'draft'` (the default) creates a draft and is LOW risk. `'send'`
 * actually delivers, and is treated as **non-idempotent**: Gmail offers no
 * idempotency key, so a timeout after dispatch records FAILED with
 * `uncertain: true` rather than resending (SPEC-002 §6). A missing email is
 * recoverable; a duplicate to a customer is not.
 */

const payloadSchema = z.object({
  to: z.array(z.string().email()).min(1, 'An email needs at least one recipient'),
  subject: z.string().min(1, 'An email needs a subject'),
  body: z.string().min(1, 'An email needs a body'),
  cc: z.array(z.string().email()).optional(),
  bcc: z.array(z.string().email()).optional(),
  sendMode: z.enum(['draft', 'send']).default('draft'),
})

export type EmailPayload = z.infer<typeof payloadSchema>

interface GmailMessage {
  id: string
  threadId?: string
  message?: { id: string; threadId?: string }
}

const AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
const TOKEN = 'https://oauth2.googleapis.com/token'
const API = 'https://gmail.googleapis.com/gmail/v1/users/me'

export const gmail: IntegrationProvider<EmailPayload, GmailMessage> = {
  id: 'gmail',
  displayName: 'Gmail',
  capability: 'EMAIL',
  auth: 'oauth',
  scopes: [
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.send',
  ],
  idempotent: false,

  isConfigured() {
    const e = env()
    return Boolean(e.GOOGLE_CLIENT_ID && e.GOOGLE_CLIENT_SECRET)
  },

  schema: payloadSchema,
  validate: (payload) => payloadSchema.parse(payload),

  preview(payload) {
    const recipients = [...payload.to, ...(payload.cc ?? [])]
    return {
      provider: 'gmail',
      consequence:
        payload.sendMode === 'send'
          ? `Sends an email immediately to ${recipients.length} recipient${recipients.length === 1 ? '' : 's'}. This cannot be undone.`
          : `Saves a draft in your Gmail. Nothing is sent until you send it.`,
      fields: [
        { label: 'Mode', value: payload.sendMode === 'send' ? 'Send now' : 'Save as draft' },
        { label: 'To', value: payload.to.join(', ') },
        ...(payload.cc?.length ? [{ label: 'Cc', value: payload.cc.join(', ') }] : []),
        { label: 'Subject', value: payload.subject },
        { label: 'Body', value: payload.body.length > 400 ? `${payload.body.slice(0, 399)}…` : payload.body },
      ],
    }
  },

  authorizeUrl(state, redirectUri, codeChallenge, app) {
    const params = new URLSearchParams({
      client_id: app.clientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: this.scopes.join(' '),
      state,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      access_type: 'offline',
      prompt: 'consent',
    })
    return `${AUTH}?${params.toString()}`
  },

  async exchangeCode(code, redirectUri, codeVerifier, app) {
    const json = await providerFetch<{
      access_token: string
      refresh_token?: string
      expires_in?: number
      scope?: string
    }>(TOKEN, {
      provider: 'gmail',
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: app.clientId,
        client_secret: app.clientSecret,
        redirect_uri: redirectUri,
        grant_type: 'authorization_code',
        code_verifier: codeVerifier,
      }),
    })
    return {
      accessToken: json.access_token,
      refreshToken: json.refresh_token,
      expiresAt: json.expires_in ? new Date(Date.now() + json.expires_in * 1000) : undefined,
      scopes: json.scope?.split(' '),
    } satisfies TokenSet
  },

  async refresh(refreshToken, app) {
    const json = await providerFetch<{ access_token: string; expires_in?: number }>(TOKEN, {
      provider: 'gmail',
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        refresh_token: refreshToken,
        client_id: app.clientId,
        client_secret: app.clientSecret,
        grant_type: 'refresh_token',
      }),
    })
    return {
      accessToken: json.access_token,
      expiresAt: json.expires_in ? new Date(Date.now() + json.expires_in * 1000) : undefined,
    }
  },

  async execute(payload, ctx) {
    const sending = payload.sendMode === 'send'
    const summary = sending
      ? `Sent “${payload.subject}” to ${payload.to.join(', ')}.`
      : `Drafted “${payload.subject}” to ${payload.to.join(', ')}.`

    if (ctx.mode === 'mock') {
      await simulateLatency()
      const id = mockId(sending ? 'msg' : 'draft', ctx.idempotencyKey)
      return {
        externalId: id,
        externalUrl: `https://mail.google.com/mail/u/0/#${sending ? 'sent' : 'drafts'}`,
        summary,
        raw: { id },
        simulated: true,
      }
    }

    if (!ctx.accessToken) {
      throw new ProviderError({
        code: 'not_connected',
        message: 'Gmail is not connected. Connect it in Settings → Integrations.',
      })
    }

    const raw = buildRfc822({ from: ctx.userEmail, ...payload })
    const endpoint = sending ? `${API}/messages/send` : `${API}/drafts`
    const body = sending ? { raw } : { message: { raw } }

    const response = await providerFetch<GmailMessage>(endpoint, {
      provider: 'gmail',
      method: 'POST',
      idempotent: false,
      headers: {
        authorization: `Bearer ${ctx.accessToken}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify(body),
    })

    const id = response.id ?? response.message?.id ?? 'unknown'
    const result: ProviderResult<GmailMessage> = {
      externalId: id,
      externalUrl: `https://mail.google.com/mail/u/0/#${sending ? 'sent' : 'drafts'}/${id}`,
      summary,
      raw: response,
      simulated: false,
    }
    return result
  },
}

/** Minimal RFC 822 message, base64url-encoded as the Gmail API expects. */
function buildRfc822(input: {
  from: string
  to: string[]
  cc?: string[]
  bcc?: string[]
  subject: string
  body: string
}): string {
  // Encoded-word the subject so non-ASCII survives; the body goes out as UTF-8.
  const subject = /[^\x20-\x7E]/.test(input.subject)
    ? `=?UTF-8?B?${Buffer.from(input.subject, 'utf8').toString('base64')}?=`
    : input.subject

  const headers = [
    `From: ${input.from}`,
    `To: ${input.to.join(', ')}`,
    ...(input.cc?.length ? [`Cc: ${input.cc.join(', ')}`] : []),
    ...(input.bcc?.length ? [`Bcc: ${input.bcc.join(', ')}`] : []),
    `Subject: ${subject}`,
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset="UTF-8"',
    'Content-Transfer-Encoding: 8bit',
  ]
  return Buffer.from(`${headers.join('\r\n')}\r\n\r\n${input.body}`, 'utf8').toString('base64url')
}
