import { z } from 'zod'
import { env } from '@/lib/env'
import { mockId, providerFetch, simulateLatency } from './http'
import type { IntegrationProvider, ProviderResult, TokenSet } from './types'
import { ProviderError } from './types'

/** Slack adapter — satisfies REMINDER actions via chat.scheduleMessage. */

const payloadSchema = z.object({
  message: z.string().min(1, 'A reminder needs a message'),
  remindAt: z.coerce.date(),
  channel: z.enum(['self', 'slack', 'email']),
  /** Slack channel id or @user; defaults to a DM with the connected user. */
  target: z.string().optional(),
})

export type ReminderPayload = z.infer<typeof payloadSchema>

interface SlackScheduled {
  ok: boolean
  scheduled_message_id?: string
  channel?: string
  error?: string
}

const AUTH = 'https://slack.com/oauth/v2/authorize'
const TOKEN = 'https://slack.com/api/oauth.v2.access'
const API = 'https://slack.com/api'

export const slack: IntegrationProvider<ReminderPayload, SlackScheduled> = {
  id: 'slack',
  displayName: 'Slack',
  capability: 'REMINDER',
  // Built and tested, but not part of what the product currently offers. Routing
  // refuses to reach it — see `resolveProvider`.
  status: 'planned' as const,

  auth: 'oauth',
  scopes: ['chat:write', 'users:read', 'im:write'],
  idempotent: true,

  isConfigured() {
    const e = env()
    return Boolean((e.SLACK_CLIENT_ID && e.SLACK_CLIENT_SECRET) || e.SLACK_BOT_TOKEN)
  },

  schema: payloadSchema,
  validate: (payload) => payloadSchema.parse(payload),

  preview(payload, ctx) {
    const where = payload.channel === 'self' ? 'you, as a direct message' : (payload.target ?? payload.channel)
    return {
      provider: 'slack',
      consequence: `Schedules a Slack message to ${where}.`,
      fields: [
        { label: 'Message', value: payload.message },
        {
          label: 'When',
          value: new Intl.DateTimeFormat('en-GB', {
            timeZone: ctx.timeZone,
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
          }).format(payload.remindAt),
        },
        { label: 'Destination', value: where },
      ],
    }
  },

  authorizeUrl(state, redirectUri, _codeChallenge, app) {
    const params = new URLSearchParams({
      client_id: app.clientId,
      redirect_uri: redirectUri,
      scope: this.scopes.join(','),
      state,
    })
    return `${AUTH}?${params.toString()}`
  },

  async exchangeCode(code, redirectUri, _codeVerifier, app) {
    const json = await providerFetch<{
      ok: boolean
      error?: string
      access_token?: string
      team?: { id: string; name: string }
      authed_user?: { id: string }
    }>(TOKEN, {
      provider: 'slack',
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: app.clientId,
        client_secret: app.clientSecret,
        redirect_uri: redirectUri,
      }),
    })
    // Slack returns HTTP 200 with ok:false for auth failures, so the status code
    // alone is not a success signal.
    if (!json.ok || !json.access_token) {
      throw new ProviderError({
        code: `slack_${json.error ?? 'oauth_failed'}`,
        message: `Slack rejected the authorization: ${json.error ?? 'unknown error'}`,
      })
    }
    return {
      accessToken: json.access_token,
      externalAccountId: json.team?.id,
      accountLabel: json.team?.name,
    } satisfies TokenSet
  },

  async refresh(): Promise<TokenSet> {
    throw new ProviderError({
      code: 'refresh_unsupported',
      message: 'Slack bot tokens do not expire; reconnect the workspace instead.',
    })
  },

  async execute(payload, ctx) {
    const summary = `Scheduled a Slack reminder for ${payload.remindAt.toISOString()}.`

    if (ctx.mode === 'mock') {
      await simulateLatency()
      const id = mockId('sched', ctx.idempotencyKey)
      return { externalId: id, summary, raw: { ok: true, scheduled_message_id: id }, simulated: true }
    }

    const token = ctx.accessToken ?? env().SLACK_BOT_TOKEN
    if (!token) {
      throw new ProviderError({
        code: 'not_connected',
        message: 'Slack is not connected. Connect it in Settings → Integrations.',
      })
    }

    const channel = payload.channel === 'self' ? (payload.target ?? ctx.userEmail) : (payload.target ?? '')
    if (!channel) {
      throw new ProviderError({
        code: 'missing_target',
        message: 'No Slack channel or user to send the reminder to.',
      })
    }

    const response = await providerFetch<SlackScheduled>(`${API}/chat.scheduleMessage`, {
      provider: 'slack',
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        channel,
        text: payload.message,
        post_at: Math.floor(payload.remindAt.getTime() / 1000),
      }),
    })

    if (!response.ok) {
      const error = response.error ?? 'unknown_error'
      throw new ProviderError({
        code: `slack_${error}`,
        message: `Slack could not schedule the message: ${error}`,
        // Slack's own transient conditions; everything else is a real rejection.
        retryable: ['ratelimited', 'service_unavailable', 'internal_error'].includes(error),
      })
    }

    const result: ProviderResult<SlackScheduled> = {
      externalId: response.scheduled_message_id ?? 'unknown',
      summary,
      raw: response,
      simulated: false,
    }
    return result
  },
}
