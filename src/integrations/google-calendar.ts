import { z } from 'zod'
import { env } from '@/lib/env'
import { mockId, providerFetch, simulateLatency } from './http'
import type { ExecutionPreview, IntegrationProvider, ProviderResult, TokenSet } from './types'
import { ProviderError } from './types'

/** Google Calendar adapter — satisfies CALENDAR actions (SPEC-002 §3.1). */

const payloadSchema = z.object({
  title: z.string().min(1, 'An event needs a title'),
  startsAt: z.coerce.date(),
  durationMinutes: z.coerce.number().int().positive().max(24 * 60),
  attendees: z.array(z.string().email()).min(1, 'An event needs at least one attendee'),
  location: z.string().optional(),
  description: z.string().optional(),
  timeZone: z.string().optional(),
})

export type CalendarPayload = z.infer<typeof payloadSchema>

interface GoogleEvent {
  id: string
  htmlLink?: string
  status?: string
}

const AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
const TOKEN = 'https://oauth2.googleapis.com/token'
const API = 'https://www.googleapis.com/calendar/v3'

export const googleCalendar: IntegrationProvider<CalendarPayload, GoogleEvent> = {
  id: 'google_calendar',
  displayName: 'Google Calendar',
  capability: 'CALENDAR',
  auth: 'oauth',
  scopes: ['https://www.googleapis.com/auth/calendar.events'],
  // Event creation with a client-supplied id is idempotent on Google's side.
  idempotent: true,

  isConfigured() {
    const e = env()
    return Boolean(e.GOOGLE_CLIENT_ID && e.GOOGLE_CLIENT_SECRET)
  },

  schema: payloadSchema,

  validate(payload) {
    return payloadSchema.parse(payload)
  },

  preview(payload, ctx) {
    const tz = payload.timeZone ?? ctx.timeZone
    const end = new Date(payload.startsAt.getTime() + payload.durationMinutes * 60_000)
    const fmt = (d: Date) =>
      new Intl.DateTimeFormat('en-GB', {
        timeZone: tz,
        weekday: 'short',
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(d)

    const preview: ExecutionPreview = {
      provider: 'google_calendar',
      consequence:
        `Creates a calendar event and emails an invitation to ` +
        `${payload.attendees.length} ${payload.attendees.length === 1 ? 'person' : 'people'}.`,
      fields: [
        { label: 'Title', value: payload.title },
        { label: 'When', value: `${fmt(payload.startsAt)} – ${fmt(end)} (${tz})` },
        { label: 'Duration', value: `${payload.durationMinutes} min` },
        { label: 'Attendees', value: payload.attendees.join(', ') },
        ...(payload.location ? [{ label: 'Location', value: payload.location }] : []),
      ],
    }
    return preview
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
      access_type: 'offline', // required to receive a refresh token
      prompt: 'consent',
    })
    return `${AUTH}?${params.toString()}`
  },

  async exchangeCode(code, redirectUri, codeVerifier, app) {
    const body = new URLSearchParams({
      code,
      client_id: app.clientId,
      client_secret: app.clientSecret,
      redirect_uri: redirectUri,
      grant_type: 'authorization_code',
      code_verifier: codeVerifier,
    })
    const json = await providerFetch<{
      access_token: string
      refresh_token?: string
      expires_in?: number
      scope?: string
    }>(TOKEN, {
      provider: 'google',
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body,
    })
    return toTokenSet(json)
  },

  async refresh(refreshToken, app) {
    const body = new URLSearchParams({
      refresh_token: refreshToken,
      client_id: app.clientId,
      client_secret: app.clientSecret,
      grant_type: 'refresh_token',
    })
    const json = await providerFetch<{ access_token: string; expires_in?: number; scope?: string }>(
      TOKEN,
      {
        provider: 'google',
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body,
      },
    )
    // Google omits refresh_token on refresh; the caller keeps the existing one.
    return toTokenSet(json)
  },

  async execute(payload, ctx) {
    const tz = payload.timeZone ?? ctx.timeZone
    const end = new Date(payload.startsAt.getTime() + payload.durationMinutes * 60_000)
    const summary =
      `Created “${payload.title}” on ` +
      `${new Intl.DateTimeFormat('en-GB', { timeZone: tz, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(payload.startsAt)} ` +
      `for ${payload.attendees.length} guest${payload.attendees.length === 1 ? '' : 's'}.`

    if (ctx.mode === 'mock') {
      await simulateLatency()
      const id = mockId('evt', ctx.idempotencyKey)
      return {
        externalId: id,
        externalUrl: `https://calendar.google.com/calendar/r/eventedit/${id}`,
        summary,
        raw: { id, status: 'confirmed' },
        simulated: true,
      }
    }

    if (!ctx.accessToken) {
      throw new ProviderError({
        code: 'not_connected',
        message: 'Google Calendar is not connected. Connect it in Settings → Integrations.',
      })
    }

    const event = await providerFetch<GoogleEvent>(
      `${API}/calendars/primary/events?sendUpdates=all&conferenceDataVersion=1`,
      {
        provider: 'google_calendar',
        method: 'POST',
        headers: {
          authorization: `Bearer ${ctx.accessToken}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          // Google accepts a client-generated id, which is what makes a retry
          // safe: the second call collides instead of creating a duplicate.
          id: googleEventId(ctx.idempotencyKey),
          summary: payload.title,
          description: payload.description,
          location: payload.location,
          start: { dateTime: payload.startsAt.toISOString(), timeZone: tz },
          end: { dateTime: end.toISOString(), timeZone: tz },
          attendees: payload.attendees.map((email) => ({ email })),
        }),
      },
    )

    const result: ProviderResult<GoogleEvent> = {
      externalId: event.id,
      externalUrl: event.htmlLink,
      summary,
      raw: event,
      simulated: false,
    }
    return result
  },
}

function toTokenSet(json: {
  access_token: string
  refresh_token?: string
  expires_in?: number
  scope?: string
}): TokenSet {
  return {
    accessToken: json.access_token,
    refreshToken: json.refresh_token,
    expiresAt: json.expires_in ? new Date(Date.now() + json.expires_in * 1000) : undefined,
    scopes: json.scope?.split(' '),
  }
}

/** Google event ids: base32hex, 5–1024 chars, lowercase. */
function googleEventId(key: string): string {
  const mapped = key.toLowerCase().replace(/[^0-9a-v]/g, '')
  return `v2b${mapped.slice(0, 40).padEnd(8, '0')}`
}
