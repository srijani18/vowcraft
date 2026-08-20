import { resolveCredential } from '@/lib/credentials/service'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'

/**
 * Transactional email — SPEC-007 §2.5.
 *
 * Two transports, resolved in order: SendGrid when a key is configured (vault first,
 * then the environment), and otherwise a log transport that writes the message and
 * hands the link back to the caller.
 *
 * The log transport is what makes password reset work on a fresh self-hosted
 * instance with no email provider. It is deliberately *not* silent about being a
 * fallback — `/api/health` reports whether real delivery is possible, so an operator
 * discovers a misconfigured production instance from a health check rather than from
 * a confused user.
 */

export type Transport = 'sendgrid' | 'log'

export interface Message {
  to: string
  subject: string
  /** Plain text. No HTML: a reset email is three sentences and a link. */
  body: string
  /** Surfaced by the log transport so a dev flow can continue without an inbox. */
  previewUrl?: string
}

export interface SendResult {
  transport: Transport
  delivered: boolean
  /** Only ever populated by the log transport, and only outside production. */
  preview?: string
  error?: string
}

const SENDER_FALLBACK = 'no-reply@vowcraft.local'

async function sendgridKey(userId: string | null): Promise<string | null> {
  // A per-user key makes no sense for a transactional send to someone who may not be
  // signed in, so the environment is the primary source here; the vault is consulted
  // only when a userId is available (an operator testing their own key).
  const fromEnv = process.env.SENDGRID_API_KEY?.trim()
  if (fromEnv) return fromEnv
  if (!userId) return null
  const resolved = await resolveCredential(userId, 'sendgrid').catch(() => null)
  return resolved?.secrets.apiKey ?? null
}

/** Whether a real send is possible right now. Reported by `/api/health`. */
export function deliveryConfigured(): boolean {
  return Boolean(process.env.SENDGRID_API_KEY?.trim())
}

export async function sendMail(
  message: Message,
  options: { userId?: string | null; requestId?: string } = {},
): Promise<SendResult> {
  const log = logger.child({ requestId: options.requestId, to: redactAddress(message.to) })
  const key = await sendgridKey(options.userId ?? null)

  if (!key) {
    // The body can contain a single-use credential, so the *link* is logged and the
    // prose is not — enough to complete the flow, without pasting the whole email
    // into a log aggregator.
    log.warn('email.log_transport', {
      subject: message.subject,
      previewUrl: message.previewUrl ?? null,
      reason: 'no SENDGRID_API_KEY configured',
    })
    return {
      transport: 'log',
      delivered: false,
      /*
       * A link in an API response is a link anybody who can reach the endpoint can
       * use, so this is behind an explicit opt-in rather than a NODE_ENV guess — the
       * local Docker stack is itself a production build, and guessing would either
       * leak in production or break the demo.
       */
      preview: env().DEV_EXPOSE_RESET_LINK ? message.previewUrl : undefined,
    }
  }

  const from = process.env.SENDGRID_FROM_EMAIL?.trim() || SENDER_FALLBACK

  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 12_000)
    const response = await fetch('https://api.sendgrid.com/v3/mail/send', {
      method: 'POST',
      headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        personalizations: [{ to: [{ email: message.to }] }],
        from: { email: from },
        subject: message.subject,
        content: [{ type: 'text/plain', value: message.body }],
      }),
      signal: controller.signal,
    }).finally(() => clearTimeout(timer))

    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      log.error('email.send_failed', { status: response.status, detail: detail.slice(0, 300) })
      return { transport: 'sendgrid', delivered: false, error: `SendGrid returned ${response.status}` }
    }

    log.info('email.sent', { transport: 'sendgrid', subject: message.subject })
    return { transport: 'sendgrid', delivered: true }
  } catch (err) {
    const aborted = (err as Error).name === 'AbortError'
    log.error('email.send_failed', { err, aborted })
    return {
      transport: 'sendgrid',
      delivered: false,
      error: aborted ? 'The email provider timed out.' : 'The email provider was unreachable.',
    }
  }
}

/** `p****@example.com` — enough to correlate a log line, not enough to harvest. */
function redactAddress(address: string): string {
  const at = address.indexOf('@')
  if (at <= 1) return '•••'
  return `${address[0]}${'•'.repeat(Math.max(3, at - 1))}${address.slice(at)}`
}

export function resetEmail(link: string, name: string | null): Message {
  const greeting = name ? `Hi ${name.split(' ')[0]},` : 'Hi,'
  return {
    to: '',
    subject: 'Reset your Vowcraft password',
    previewUrl: link,
    body:
      `${greeting}\n\n` +
      `Someone asked to reset the password on your Vowcraft account. If that was you, ` +
      `open the link below within the next hour:\n\n` +
      `${link}\n\n` +
      `The link works once. Using it will also sign you out everywhere else, which is ` +
      `deliberate — if you did not request this, whoever did should not keep a session.\n\n` +
      `If it was not you, you can ignore this email. Your password has not changed.\n`,
  }
}

export function welcomeEmail(name: string | null, appUrl: string, method: 'password' | 'google'): Message {
  const greeting = name ? `Welcome, ${name.split(' ')[0]}.` : 'Welcome.'
  const signInNote =
    method === 'google'
      ? `You signed up with Google, so there is no password to remember. You can add one from ` +
        `Settings → Profile if you would rather have both.`
      : `You signed up with an email and password. If you ever lose it, the sign-in page has a ` +
        `reset link.`

  return {
    to: '',
    subject: 'Welcome to Vowcraft',
    body:
      `${greeting}\n\n` +
      `Vowcraft turns what a meeting agreed to into calendar invites, tasks and drafts — but ` +
      `nothing is sent until you approve it. Three things worth knowing before you start:\n\n` +
      `1. You are in mock mode. Every action runs end to end and produces a labelled simulated ` +
      `result, so you can walk the whole approval path before connecting a single account.\n\n` +
      `2. Every extracted action shows the timestamp and the exact quote it came from, so you can ` +
      `judge whether it is real without replaying the meeting.\n\n` +
      `3. Guardrails run on the server, every time. No weekend meetings, nothing outside your ` +
      `working hours, no external email without your explicit approval. You set the envelope in ` +
      `Settings → Preferences.\n\n` +
      `Start here: ${appUrl}/dashboard\n\n` +
      `${signInNote}\n`,
  }
}
