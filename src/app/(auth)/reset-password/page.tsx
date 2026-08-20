import type { Metadata } from 'next'
import Link from 'next/link'
import { ResetPasswordForm } from '@/components/auth/ResetPasswordForm'
import { inspectToken } from '@/server/auth/reset'
import { sessionsAvailable } from '@/lib/session'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Set a new password — Voice2BRD',
  // A page reachable only with a single-use credential in its URL must never be
  // indexed, and the global Referrer-Policy keeps the token out of referrers.
  robots: { index: false, follow: false, nocache: true },
}

const REASON_COPY = {
  unknown: {
    title: 'That link is not valid',
    body: 'It may have been mistyped, or already replaced by a newer one. Request a fresh link and it will work.',
  },
  expired: {
    title: 'That link has expired',
    body: 'Reset links last an hour, which is deliberate — a stale one sitting in an inbox should not be a standing key.',
  },
  used: {
    title: 'That link has already been used',
    body: 'Each link works once. If you did not just reset your password, request a new link and change it now.',
  },
} as const

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const { token } = await searchParams

  // Validated on load rather than on submit, so nobody types a new password twice
  // only to be told the link was dead all along.
  const check = token ? await inspectToken(token) : { valid: false, reason: 'unknown' as const }

  if (!check.valid) {
    const copy = REASON_COPY[check.reason ?? 'unknown']
    return (
      <div className="panel rounded-2xl p-6 sm:p-8">
        <i className="bi bi-link-45deg text-3xl text-warn" aria-hidden />
        <h1 className="mt-4 text-lg font-semibold">{copy.title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">{copy.body}</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            href="/forgot-password"
            className="glow-cta inline-flex items-center gap-2 rounded-xl border border-cta-edge bg-cta px-4 py-2.5 text-sm font-semibold text-cta-on"
          >
            Request a new link
            <i className="bi bi-arrow-right text-xs" aria-hidden />
          </Link>
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-xl border border-edge/30 bg-surface-strong px-4 py-2.5 text-sm font-medium"
          >
            Back to sign in
          </Link>
        </div>
      </div>
    )
  }

  return (
    <ResetPasswordForm
      token={token!}
      email={check.email!}
      isFirstPassword={!check.hasPassword}
      sessionsAvailable={sessionsAvailable()}
    />
  )
}
