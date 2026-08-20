'use client'

import { useState } from 'react'
import { PasswordField } from './PasswordField'
import { MIN_PASSWORD_LENGTH, validateConfirmation, validatePassword } from '@/lib/password-rules'

/**
 * Set a new password from a reset link.
 *
 * Two details worth naming. The token stays in the URL and is submitted from there
 * rather than being copied into a hidden field — one place for it, and the page is
 * already `noindex, nocache`. And the copy adapts: someone whose account has no
 * password (a Google sign-up following the link) is *setting* one, not *resetting*
 * one, and being told otherwise is confusing.
 */
export function ResetPasswordForm({
  token,
  email,
  isFirstPassword,
  sessionsAvailable,
}: {
  token: string
  email: string
  isFirstPassword: boolean
  sessionsAvailable: boolean
}) {
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)

    const found: Record<string, string> = {}
    const problem = validatePassword(password, { email })
    if (problem) found.password = problem.message
    const mismatch = validateConfirmation(password, confirmPassword)
    if (mismatch) found.confirmPassword = mismatch.message

    setErrors(found)
    if (Object.keys(found).length > 0) {
      document.getElementById(`auth-${Object.keys(found)[0]}`)?.focus()
      setRevealed(false)
      return
    }

    setSubmitting(true)
    try {
      const response = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ token, newPassword: password }),
      })
      const json = (await response.json()) as {
        ok?: boolean
        redirectTo?: string
        error?: { code: string; message: string }
      }

      if (!response.ok || !json.ok) {
        const code = json.error?.code ?? ''
        if (code.startsWith('password_')) {
          setErrors({ password: json.error!.message })
          document.getElementById('auth-password')?.focus()
        } else {
          setFormError(json.error?.message ?? 'Something went wrong. Please try again.')
        }
        return
      }

      setDone(true)
      // A full navigation: the session cookie was just set and every server component
      // needs to re-render with the new identity.
      window.location.assign(json.redirectTo ?? '/dashboard')
    } catch {
      setFormError('Could not reach the server. Check your connection and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <div className="panel lit-edge rounded-2xl p-8 text-center">
        <i className="bi bi-shield-check text-3xl text-ok" aria-hidden />
        <h1 className="mt-4 text-lg font-semibold">
          {isFirstPassword ? 'Password set' : 'Password changed'}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          Taking you to your dashboard. If nothing happens,{' '}
          <a href="/dashboard" className="font-medium text-accent hover:underline">
            go there directly
          </a>
          .
        </p>
      </div>
    )
  }

  return (
    <div className="panel lit-edge rounded-2xl p-6 sm:p-8">
      <h1 className="text-xl font-semibold tracking-tight">
        {isFirstPassword ? 'Set a password' : 'Choose a new password'}
      </h1>
      <p className="mt-1.5 text-sm text-ink-muted">
        For <span className="font-medium text-ink">{email}</span>.
        {isFirstPassword
          ? ' This account signs in with Google today — adding a password gives you both.'
          : ''}
      </p>

      {!sessionsAvailable && (
        <p className="mt-4 flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-[11px] leading-relaxed text-danger">
          <i className="bi bi-shield-slash-fill mt-0.5 shrink-0" aria-hidden />
          <span>
            <span className="font-semibold">APP_ENCRYPTION_KEY is not configured,</span> so this cannot
            complete. Ask an administrator to set one.
          </span>
        </p>
      )}

      <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
        <PasswordField
          id="auth-password"
          label="New password"
          value={password}
          onChange={setPassword}
          revealed={revealed}
          onRevealChange={setRevealed}
          error={errors.password}
          autoComplete="new-password"
          showMeter
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. A memorable phrase beats a short scramble.`}
        />

        <PasswordField
          id="auth-confirmPassword"
          label="Confirm new password"
          value={confirmPassword}
          onChange={setConfirmPassword}
          revealed={revealed}
          onRevealChange={setRevealed}
          error={errors.confirmPassword}
          autoComplete="new-password"
        />

        {formError && (
          <p role="alert" className="flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs text-danger">
            <i className="bi bi-exclamation-triangle-fill mt-0.5 shrink-0" aria-hidden />
            {formError}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || !sessionsAvailable}
          className="glow-cta flex w-full items-center justify-center gap-2 rounded-xl border border-cta-edge bg-cta px-4 py-2.5 text-sm font-semibold text-cta-on transition-all hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? (
            <>
              <i className="bi bi-arrow-repeat animate-spin" aria-hidden />
              Saving…
            </>
          ) : (
            <>
              {isFirstPassword ? 'Set password and sign in' : 'Change password and sign in'}
              <i className="bi bi-arrow-right text-xs" aria-hidden />
            </>
          )}
        </button>
      </form>

      <p className="mt-4 text-[11px] leading-relaxed text-ink-faint">
        <i className="bi bi-info-circle mr-1" aria-hidden />
        This will sign you out on every other device. That is deliberate — if someone else
        requested this reset, they should not keep a session you cannot see.
      </p>
    </div>
  )
}
