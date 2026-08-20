'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useState } from 'react'
import { validateEmail } from '@/lib/password-rules'

/**
 * Request a reset link.
 *
 * The confirmation is deliberately vague about whether the address exists — matching
 * the API, which returns the same body either way (SPEC-007 §2.3). Saying "we sent
 * you an email" only when the account is real would turn this page into an account
 * enumerator, so it says "if that address has an account" and means it.
 */
export function ForgotPasswordForm({ canShowDevLink }: { canShowDevLink: boolean }) {
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState<{ delivered: boolean; devLink?: string } | null>(null)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)

    const problem = validateEmail(email)
    if (problem) {
      setError(problem.message)
      document.getElementById('forgot-email')?.focus()
      return
    }
    setError(null)
    setSubmitting(true)

    try {
      const response = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      const json = (await response.json()) as {
        ok?: boolean
        delivered?: boolean
        devLink?: string
        error?: { message: string }
      }
      if (!response.ok || !json.ok) {
        setFormError(json.error?.message ?? 'Something went wrong. Please try again.')
        return
      }
      setSent({ delivered: Boolean(json.delivered), devLink: json.devLink })
    } catch {
      setFormError('Could not reach the server. Check your connection and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  if (sent) {
    return (
      <div className="panel lit-edge rounded-2xl p-6 sm:p-8">
        <i className="bi bi-envelope-check-fill text-3xl text-accent" aria-hidden />
        <h1 className="mt-4 text-lg font-semibold">Check your inbox</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">
          If <span className="font-medium text-ink">{email}</span> has an account, a reset link is on
          its way. It works once and expires in an hour.
        </p>

        {!sent.delivered && canShowDevLink && sent.devLink && (
          <div className="mt-5 rounded-xl border border-warn/40 bg-warn/[0.08] p-3.5">
            <p className="text-xs font-medium text-warn">
              <i className="bi bi-tools mr-1.5" aria-hidden />
              No mail provider configured
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
              This deployment has no <code className="font-mono">SENDGRID_API_KEY</code>, so nothing was
              emailed. Here is the link you would have received — shown only because this is not
              production.
            </p>
            <a
              href={sent.devLink}
              className="mt-2.5 inline-flex items-center gap-1.5 break-all text-xs font-medium text-accent hover:underline"
            >
              Continue to reset your password
              <i className="bi bi-arrow-right text-[10px]" aria-hidden />
            </a>
          </div>
        )}

        {!sent.delivered && !canShowDevLink && (
          <p className="mt-4 text-[11px] leading-relaxed text-ink-faint">
            If nothing arrives, check your spam folder — or ask an administrator, since this
            deployment may not have email configured.
          </p>
        )}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Link href="/login" className="text-sm font-medium text-accent hover:underline">
            Back to sign in
          </Link>
          <button
            onClick={() => setSent(null)}
            className="text-sm text-ink-muted hover:text-ink"
          >
            Use a different address
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="panel lit-edge rounded-2xl p-6 sm:p-8">
      <h1 className="text-xl font-semibold tracking-tight">Forgotten your password?</h1>
      <p className="mt-1.5 text-sm text-ink-muted">
        Give us the address on your account and we will send a link to set a new one.
      </p>

      <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
        <div>
          <label htmlFor="forgot-email" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Email address
          </label>
          <input
            id="forgot-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setError(validateEmail(email)?.message ?? null)}
            autoComplete="email"
            inputMode="email"
            autoFocus
            placeholder="you@company.com"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'forgot-email-error' : undefined}
            className={clsx(
              'w-full rounded-xl border bg-base px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint',
              'focus-visible:outline-none focus-visible:ring-2',
              error
                ? 'border-danger/60 focus-visible:border-danger focus-visible:ring-danger/30'
                : 'border-edge/30 focus-visible:border-accent-fill focus-visible:ring-accent-fill/40',
            )}
          />
          {error && (
            <p id="forgot-email-error" role="alert" className="mt-1.5 flex items-start gap-1.5 text-xs text-danger">
              <i className="bi bi-exclamation-circle-fill mt-0.5 shrink-0" aria-hidden />
              {error}
            </p>
          )}
        </div>

        {formError && (
          <p role="alert" className="flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs text-danger">
            <i className="bi bi-exclamation-triangle-fill mt-0.5 shrink-0" aria-hidden />
            {formError}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="glow-cta flex w-full items-center justify-center gap-2 rounded-xl border border-cta-edge bg-cta px-4 py-2.5 text-sm font-semibold text-cta-on transition-all hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? (
            <>
              <i className="bi bi-arrow-repeat animate-spin" aria-hidden />
              Sending…
            </>
          ) : (
            <>
              Send the reset link
              <i className="bi bi-arrow-right text-xs" aria-hidden />
            </>
          )}
        </button>
      </form>

      <p className="mt-5 text-center text-xs text-ink-muted">
        Remembered it?{' '}
        <Link href="/login" className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  )
}
