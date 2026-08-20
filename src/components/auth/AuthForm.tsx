'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useState } from 'react'
import { AuthDivider, GoogleButton } from './GoogleButton'
import { PasswordField } from './PasswordField'
import { apiBaseUrl, apiConfigured, storeTokens } from '@/lib/api-client'
import {
  MIN_PASSWORD_LENGTH,
  validateConfirmation,
  validateEmail,
  validateName,
  validatePassword,
  type FieldError,
} from '@/lib/password-rules'

/**
 * Login and signup, sharing one component because they differ by three fields and
 * a verb — two near-identical forms drift apart, and the drift always lands on the
 * validation.
 *
 * Validation strategy: validate on **submit and on blur**, never on every
 * keystroke. Telling someone their password is too short while they are still on
 * the fourth character is noise, and it trains people to ignore the message that
 * eventually matters.
 *
 * Errors are surfaced three ways because each catches a different reader: inline
 * text tied to the input with `aria-describedby`, `aria-invalid` on the control,
 * and focus moved to the first offending field.
 */

type Mode = 'login' | 'signup'

interface Props {
  mode: Mode
  /** Where to land after success. Carried through from ?next= when present. */
  next?: string
  /** False when APP_ENCRYPTION_KEY is missing, so sessions cannot be signed. */
  sessionsAvailable: boolean
  /** Only show the Google button when the OAuth app is actually configured. */
  googleAvailable: boolean
  /** A message carried back from a failed federated attempt. */
  initialError?: string
}

const COPY = {
  login: {
    title: 'Welcome back',
    subtitle: 'Pick up where the last meeting left off.',
    submit: 'Log in',
    switchPrompt: 'New here?',
    switchLabel: 'Create an account',
    switchHref: '/signup',
  },
  signup: {
    title: 'Create your account',
    subtitle: 'Start in mock mode — nothing reaches the outside world until you say so.',
    submit: 'Create account',
    switchPrompt: 'Already have an account?',
    switchLabel: 'Log in',
    switchHref: '/login',
  },
} as const

export function AuthForm({ mode, next, sessionsAvailable, googleAvailable, initialError }: Props) {
  const copy = COPY[mode]
  const isSignup = mode === 'signup'

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [accepted, setAccepted] = useState(false)

  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(initialError ?? null)
  const [submitting, setSubmitting] = useState(false)
  const [handedOff, setHandedOff] = useState(false)

  const setFieldError = (problem: FieldError | null, field: string) =>
    setErrors((prev) => {
      const next = { ...prev }
      if (problem) next[field] = problem.message
      else delete next[field]
      return next
    })

  /** Full validation, returned as a map so the caller can focus the first field. */
  function validateAll(): Record<string, string> {
    const found: Record<string, string> = {}
    const push = (problem: FieldError | null) => {
      if (problem && !found[problem.field]) found[problem.field] = problem.message
    }

    if (isSignup) push(validateName(name))
    push(validateEmail(email))

    if (isSignup) {
      push(validatePassword(password, { email, name }))
      push(validateConfirmation(password, confirmPassword))
      if (!accepted) found.accepted = 'Please accept the terms to continue.'
    } else if (!password) {
      // On login the server is the only authority on whether a password is right,
      // so the form checks presence and nothing more. Applying the strength policy
      // here would lock out anyone whose password predates a policy change.
      found.password = 'Enter your password.'
    }

    return found
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)

    const found = validateAll()
    setErrors(found)

    if (Object.keys(found).length > 0) {
      const firstField = Object.keys(found)[0]!
      document.getElementById(`auth-${firstField}`)?.focus()
      // Hide a revealed password once the form is in an error state — the reader's
      // attention is about to move to the message, not the characters.
      setRevealed(false)
      return
    }

    setSubmitting(true)
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(isSignup ? { name, email, password } : { email, password }),
      })
      const json = (await response.json()) as {
        ok?: boolean
        redirectTo?: string
        error?: { code: string; message: string; details?: unknown }
      }

      if (!response.ok || !json.ok) {
        const error = json.error
        // Map the server's stable code back onto the field it belongs to, so a
        // rejection lands next to the input that caused it rather than in a banner.
        const field = fieldForCode(error?.code)
        if (field) {
          setErrors({ [field]: error!.message })
          document.getElementById(`auth-${field}`)?.focus()
        } else {
          setFormError(error?.message ?? 'Something went wrong. Please try again.')
        }
        return
      }

      /*
       * Also authenticate against the FastAPI backend, and keep its token.
       *
       * Transitional, and deliberately so: the Next.js route above set the session cookie
       * that every server-rendered page still reads, while the backend issues the bearer
       * token that cross-origin calls need — a `SameSite=Lax` cookie is not sent to another
       * origin at all. Both are needed until every route has moved.
       *
       * A failure here is not fatal to signing in. The user reaches the app either way;
       * only the features already served by the backend are unavailable, and those say so
       * themselves rather than failing silently.
       */
      if (apiConfigured()) {
        try {
          const backend = await fetch(`${apiBaseUrl()}/api/auth/${mode}`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(isSignup ? { name, email, password } : { email, password }),
          })
          if (backend.ok) {
            const issued = (await backend.json()) as {
              tokens?: { accessToken: string; refreshToken: string }
            }
            if (issued.tokens) storeTokens(issued.tokens)
          }
        } catch {
          // Backend unreachable. Signing in still succeeded; nothing to surface here.
        }
      }

      setHandedOff(true)
      // A full navigation, not a router push: the session cookie was just set and
      // every server component needs to re-render with the new identity.
      window.location.assign(next && next.startsWith('/') ? next : (json.redirectTo ?? '/dashboard'))
    } catch {
      // A network failure is distinct from a rejected credential, and saying so
      // stops someone retyping a password that was never the problem.
      setFormError('Could not reach the server. Check your connection and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  if (handedOff) {
    return (
      <div className="panel lit-edge rounded-2xl p-8 text-center">
        <i className="bi bi-arrow-right-circle-fill text-3xl text-accent" aria-hidden />
        <h2 className="mt-4 text-lg font-semibold">
          {isSignup ? 'Account created' : 'Signed in'}
        </h2>
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
      <h1 className="text-xl font-semibold tracking-tight">{copy.title}</h1>
      <p className="mt-1.5 text-sm text-ink-muted">{copy.subtitle}</p>

      {!sessionsAvailable && (
        <p className="mt-4 flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-[11px] leading-relaxed text-danger">
          <i className="bi bi-shield-slash-fill mt-0.5 shrink-0" aria-hidden />
          <span>
            <span className="font-semibold">APP_ENCRYPTION_KEY is not set,</span> so sessions cannot be
            signed and sign-in is unavailable. Generate one with{' '}
            <code className="rounded bg-abyss/20 px-1 font-mono">openssl rand -base64 32</code> and
            restart.
          </span>
        </p>
      )}

      {googleAvailable && (
        <>
          {/* Offered first because it is one click. Making someone scroll past a form
              to find the faster path is backwards. */}
          <div className="mt-6">
            <GoogleButton next={next} label={isSignup ? 'Sign up with Google' : 'Continue with Google'} />
          </div>
          <AuthDivider />
        </>
      )}

      <form onSubmit={onSubmit} noValidate className={googleAvailable ? 'space-y-4' : 'mt-6 space-y-4'}>
        {isSignup && (
          <div>
            <label htmlFor="auth-name" className="mb-1.5 block text-xs font-medium text-ink-muted">
              Your name
            </label>
            <input
              id="auth-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => setFieldError(validateName(name), 'name')}
              autoComplete="name"
              placeholder="Priya Raman"
              aria-invalid={errors.name ? true : undefined}
              aria-describedby={errors.name ? 'auth-name-error' : undefined}
              className={clsx(
                'w-full rounded-xl border bg-base px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint',
                'focus-visible:outline-none focus-visible:ring-2',
                errors.name
                  ? 'border-danger/60 focus-visible:border-danger focus-visible:ring-danger/30'
                  : 'border-edge/30 focus-visible:border-accent-fill focus-visible:ring-accent-fill/40',
              )}
            />
            {errors.name && (
              <p id="auth-name-error" role="alert" className="mt-1.5 flex items-start gap-1.5 text-xs text-danger">
                <i className="bi bi-exclamation-circle-fill mt-0.5 shrink-0" aria-hidden />
                {errors.name}
              </p>
            )}
          </div>
        )}

        <div>
          <label htmlFor="auth-email" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Work email
          </label>
          <input
            id="auth-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setFieldError(validateEmail(email), 'email')}
            autoComplete="email"
            inputMode="email"
            placeholder="you@company.com"
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? 'auth-email-error' : undefined}
            className={clsx(
              'w-full rounded-xl border bg-base px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint',
              'focus-visible:outline-none focus-visible:ring-2',
              errors.email
                ? 'border-danger/60 focus-visible:border-danger focus-visible:ring-danger/30'
                : 'border-edge/30 focus-visible:border-accent-fill focus-visible:ring-accent-fill/40',
            )}
          />
          {errors.email && (
            <p id="auth-email-error" role="alert" className="mt-1.5 flex items-start gap-1.5 text-xs text-danger">
              <i className="bi bi-exclamation-circle-fill mt-0.5 shrink-0" aria-hidden />
              {errors.email}
            </p>
          )}
        </div>

        <PasswordField
          id="auth-password"
          label="Password"
          value={password}
          onChange={setPassword}
          onRevealChange={setRevealed}
          revealed={revealed}
          error={errors.password}
          autoComplete={isSignup ? 'new-password' : 'current-password'}
          showMeter={isSignup}
          hint={isSignup ? `At least ${MIN_PASSWORD_LENGTH} characters. A memorable phrase beats a short scramble.` : undefined}
        />

        {isSignup && (
          <PasswordField
            id="auth-confirmPassword"
            label="Confirm password"
            value={confirmPassword}
            onChange={setConfirmPassword}
            onRevealChange={setRevealed}
            revealed={revealed}
            error={errors.confirmPassword}
            autoComplete="new-password"
          />
        )}

        {isSignup && (
          <div>
            <label className="flex cursor-pointer items-start gap-2.5">
              <input
                id="auth-accepted"
                type="checkbox"
                checked={accepted}
                onChange={(e) => {
                  setAccepted(e.target.checked)
                  if (e.target.checked) setFieldError(null, 'accepted')
                }}
                aria-invalid={errors.accepted ? true : undefined}
                className="mt-0.5 size-4 shrink-0 accent-accent-fill"
              />
              <span className="text-xs leading-relaxed text-ink-muted">
                I understand this tool can act on my behalf, and that every action requires my approval
                unless I explicitly opt in to auto-execution.
              </span>
            </label>
            {errors.accepted && (
              <p role="alert" className="mt-1.5 flex items-start gap-1.5 text-xs text-danger">
                <i className="bi bi-exclamation-circle-fill mt-0.5 shrink-0" aria-hidden />
                {errors.accepted}
              </p>
            )}
          </div>
        )}

        {!isSignup && (
          <div className="flex justify-end">
            <Link href="/forgot-password" className="text-xs text-ink-muted hover:text-accent hover:underline">
              Forgotten your password?
            </Link>
          </div>
        )}

        {formError && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs text-danger"
          >
            <i className="bi bi-exclamation-triangle-fill mt-0.5 shrink-0" aria-hidden />
            {formError}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || !sessionsAvailable}
          className="glow-accent flex w-full items-center justify-center gap-2 rounded-xl bg-accent-fill px-4 py-2.5 text-sm font-semibold text-accent-on transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? (
            <>
              <i className="bi bi-arrow-repeat animate-spin" aria-hidden />
              Just a moment…
            </>
          ) : (
            <>
              {copy.submit}
              <i className="bi bi-arrow-right text-xs" aria-hidden />
            </>
          )}
        </button>
      </form>

      <p className="mt-5 text-center text-xs text-ink-muted">
        {copy.switchPrompt}{' '}
        <Link href={copy.switchHref} className="font-medium text-accent hover:underline">
          {copy.switchLabel}
        </Link>
      </p>
    </div>
  )
}

/**
 * The server returns a stable `code`; this maps it back to the field it concerns.
 * Codes not listed here are genuinely form-level (invalid credentials, service
 * unavailable) and belong in the banner.
 */
function fieldForCode(code: string | undefined): string | null {
  if (!code) return null
  if (code.startsWith('password_') || code === 'missing_password') return 'password'
  if (code.startsWith('confirmation_')) return 'confirmPassword'
  if (code.startsWith('email_') || code === 'account_exists') return 'email'
  if (code.startsWith('name_')) return 'name'
  return null
}
