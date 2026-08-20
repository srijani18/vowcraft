import { z } from 'zod'
import { mockId, providerFetchRaw, simulateLatency } from './http'
import type { IntegrationProvider, ProviderResult, TokenSet } from './types'
import { ProviderError } from './types'

/**
 * SendGrid adapter — the second EMAIL provider, alongside Gmail.
 *
 * Why both: Gmail sends *as the user* and can save a draft, which is the safe
 * default for anything a human might want to read before it goes out. SendGrid
 * sends *as the organisation* from a verified domain, needs no per-user OAuth
 * consent, and works on a server with no interactive login — which is what makes
 * it the right choice for scheduled or unattended sends.
 *
 * The important consequence of that difference: **SendGrid cannot save a draft.**
 * `sendMode: 'draft'` is classified LOW risk precisely because nothing is sent
 * (SPEC-003 §3), so silently delivering a "draft" through SendGrid would turn a
 * LOW-risk approval into a real outbound email. `validate()` refuses that
 * combination outright rather than downgrading it.
 */

const payloadSchema = z
  .object({
    to: z.array(z.string().email()).min(1, 'An email needs at least one recipient'),
    subject: z.string().min(1, 'An email needs a subject'),
    body: z.string().min(1, 'An email needs a body'),
    cc: z.array(z.string().email()).optional(),
    bcc: z.array(z.string().email()).optional(),
    sendMode: z.enum(['draft', 'send']).default('send'),
    /** Verified sender. Falls back to SENDGRID_FROM_EMAIL, then the user. */
    from: z.string().email().optional(),
  })
  .refine((v) => v.sendMode !== 'draft', {
    path: ['sendMode'],
    message:
      'SendGrid cannot save drafts — it only delivers. Route this action to Gmail to draft it, ' +
      'or set the send mode to “send” and approve it as a real send.',
  })

export type SendGridPayload = z.infer<typeof payloadSchema>

interface SendGridResult {
  messageId: string | null
  status: number
}

const API = 'https://api.sendgrid.com/v3'

export const sendgrid: IntegrationProvider<SendGridPayload, SendGridResult> = {
  id: 'sendgrid',
  displayName: 'SendGrid',
  capability: 'EMAIL',
  auth: 'api_key',
  credentialService: 'sendgrid',
  scopes: [],
  // No idempotency key on the send endpoint; a retry after an uncertain failure
  // could deliver twice, so uncertain failures are never retried (SPEC-002 §6).
  idempotent: false,

  isConfigured() {
    // The key may live in the vault per user rather than in the environment, so
    // this only reports the deployment-wide default. A per-user key is resolved
    // at execute time and reported by the credentials page.
    return Boolean(process.env.SENDGRID_API_KEY)
  },

  schema: payloadSchema,
  validate: (payload) => payloadSchema.parse(payload),

  preview(payload, ctx) {
    const recipients = [...payload.to, ...(payload.cc ?? []), ...(payload.bcc ?? [])]
    const from = payload.from ?? process.env.SENDGRID_FROM_EMAIL ?? ctx.userEmail
    return {
      provider: 'sendgrid',
      consequence:
        `Delivers an email immediately to ${recipients.length} recipient` +
        `${recipients.length === 1 ? '' : 's'} from ${from}. This cannot be undone.`,
      fields: [
        { label: 'From', value: from },
        { label: 'To', value: payload.to.join(', ') },
        ...(payload.cc?.length ? [{ label: 'Cc', value: payload.cc.join(', ') }] : []),
        ...(payload.bcc?.length ? [{ label: 'Bcc', value: payload.bcc.join(', ') }] : []),
        { label: 'Subject', value: payload.subject },
        {
          label: 'Body',
          value: payload.body.length > 400 ? `${payload.body.slice(0, 399)}…` : payload.body,
        },
      ],
    }
  },

  // SendGrid authenticates with a long-lived API key, so there is no consent
  // redirect and nothing to refresh. These three exist to satisfy the interface
  // and fail loudly rather than silently doing nothing.
  authorizeUrl(): string {
    throw new ProviderError({
      code: 'oauth_unsupported',
      message: 'SendGrid uses an API key, not OAuth. Add it in Settings → API keys.',
    })
  },

  async exchangeCode(): Promise<TokenSet> {
    throw new ProviderError({
      code: 'oauth_unsupported',
      message: 'SendGrid uses an API key, not OAuth.',
    })
  },

  async refresh(): Promise<TokenSet> {
    throw new ProviderError({
      code: 'refresh_unsupported',
      message: 'SendGrid API keys do not expire; replace the key if it was revoked.',
    })
  },

  async execute(payload, ctx) {
    const from = payload.from ?? process.env.SENDGRID_FROM_EMAIL ?? ctx.userEmail
    const summary = `Sent “${payload.subject}” to ${payload.to.join(', ')} via SendGrid.`

    if (ctx.mode === 'mock') {
      await simulateLatency()
      const id = mockId('sg', ctx.idempotencyKey)
      return {
        externalId: id,
        externalUrl: 'https://app.sendgrid.com/email_activity',
        summary,
        raw: { messageId: id, status: 202 },
        simulated: true,
      }
    }

    if (!ctx.accessToken) {
      throw new ProviderError({
        code: 'not_connected',
        message: 'No SendGrid API key configured. Add one in Settings → API keys.',
      })
    }

    const response = await providerFetchRaw(`${API}/mail/send`, {
      provider: 'sendgrid',
      method: 'POST',
      idempotent: false,
      headers: {
        authorization: `Bearer ${ctx.accessToken}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        personalizations: [
          {
            to: payload.to.map((email) => ({ email })),
            ...(payload.cc?.length ? { cc: payload.cc.map((email) => ({ email })) } : {}),
            ...(payload.bcc?.length ? { bcc: payload.bcc.map((email) => ({ email })) } : {}),
          },
        ],
        from: { email: from },
        subject: payload.subject,
        content: [{ type: 'text/plain', value: payload.body }],
        // Surfaces in SendGrid's activity feed, which is how a support engineer
        // ties a delivered message back to the action item that produced it.
        custom_args: { vowcraft_idempotency_key: ctx.idempotencyKey },
      }),
    })

    // A successful send is 202 Accepted with an empty body; the id is a header.
    const messageId = response.headers.get('x-message-id')
    const result: ProviderResult<SendGridResult> = {
      externalId: messageId ?? `sg_${ctx.idempotencyKey.slice(-12)}`,
      externalUrl: 'https://app.sendgrid.com/email_activity',
      summary,
      raw: { messageId, status: response.status },
      simulated: false,
    }
    return result
  },
}
