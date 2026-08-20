'use client'

import clsx from 'clsx'
import { useState } from 'react'
import { Badge, Button, Field, GlassCard, inputClass } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch, storeTokens } from '@/lib/api-client'
import type { ProfileView } from '@/server/profile/service'

/**
 * Profile management — SPEC-005 §3, §4, §8.
 *
 * Deliberate omissions, each stated in the UI rather than silently missing:
 *  - **Email is not editable.** It is the account identifier and the subject of
 *    every OAuth grant, so changing it would orphan connected integrations.
 *  - **No "show password" on the current-password field.** It is typed once to
 *    prove identity; revealing it earns nothing and leaks to a shoulder.
 *  - **Deletion needs the email typed out.** A checkbox is too easy to click
 *    through for something irreversible.
 */

export function ProfileManager({ initial }: { initial: ProfileView }) {
  const toast = useToast()
  const [profile, setProfile] = useState(initial)

  const [name, setName] = useState(initial.name ?? '')
  const [savingName, setSavingName] = useState(false)

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [savingPassword, setSavingPassword] = useState(false)
  const [passwordError, setPasswordError] = useState<string | null>(null)

  const [deleteConfirm, setDeleteConfirm] = useState('')
  const [deleting, setDeleting] = useState(false)

  async function call<T>(url: string, method: string, body?: unknown): Promise<T> {
    const response = await apiFetch(url, {
      method,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    const json = (await response.json()) as T & { error?: { message: string } }
    if (!response.ok) throw new Error(json.error?.message ?? 'Request failed')
    return json
  }

  const saveName = async () => {
    setSavingName(true)
    try {
      const next = await call<ProfileView>('/api/profile', 'PATCH', { name: name.trim() })
      setProfile(next)
      toast.push({ tone: 'success', title: 'Name updated', detail: 'It appears on cards and in the audit trail.' })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not save your name', detail: (err as Error).message })
    } finally {
      setSavingName(false)
    }
  }

  const savePassword = async () => {
    setPasswordError(null)
    // Checked here purely to spare a round-trip; the server is the authority on
    // every other rule.
    if (newPassword !== confirmPassword) {
      setPasswordError('The two new passwords do not match.')
      return
    }
    setSavingPassword(true)
    try {
      // Rotating the hash invalidates every existing token, including the one this
      // request authenticated with — the response is a fresh pair, and it must be
      // stored immediately or the very next authenticated call 401s.
      const tokens = await call<{ accessToken: string; refreshToken: string }>(
        '/api/auth/change-password',
        'POST',
        { ...(profile.hasPassword ? { currentPassword } : {}), newPassword },
      )
      storeTokens(tokens)
      const next = await call<ProfileView>('/api/profile', 'GET')
      setProfile(next)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      toast.push({
        tone: 'success',
        title: profile.hasPassword ? 'Password changed' : 'Password set',
        detail: 'Hashed with scrypt. It is never stored or transmitted in the clear.',
      })
    } catch (err) {
      setPasswordError((err as Error).message)
    } finally {
      setSavingPassword(false)
    }
  }

  const requestDeletion = async () => {
    setDeleting(true)
    try {
      const next = await call<ProfileView>('/api/profile', 'DELETE', { confirm: deleteConfirm.trim() })
      setProfile(next)
      setDeleteConfirm('')
      toast.push({
        tone: 'info',
        title: 'Deletion scheduled',
        detail: 'You have 7 days to change your mind. Nothing has been removed yet.',
      })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not schedule deletion', detail: (err as Error).message })
    } finally {
      setDeleting(false)
    }
  }

  const cancelDeletion = async () => {
    setDeleting(true)
    try {
      const next = await call<ProfileView>('/api/profile/restore', 'POST')
      setProfile(next)
      toast.push({ tone: 'success', title: 'Deletion cancelled', detail: 'Your account is safe.' })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not cancel', detail: (err as Error).message })
    } finally {
      setDeleting(false)
    }
  }

  const strength = describeStrength(newPassword, profile.minPasswordLength)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Profile</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Who you are. What the agent may do on your behalf lives under{' '}
          <a href="/dashboard/settings" className="font-medium text-accent hover:underline">
            Preferences
          </a>
          .
        </p>
      </header>

      {/* ── identity */}
      <GlassCard className="p-5">
        <div className="flex items-start gap-4">
          <span className="grid size-14 shrink-0 place-items-center rounded-2xl bg-accent-fill text-lg font-semibold text-accent-on">
            {(profile.name ?? profile.email).slice(0, 2).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">Identity</h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              Member since{' '}
              {new Date(profile.createdAt).toLocaleDateString(undefined, {
                day: 'numeric',
                month: 'long',
                year: 'numeric',
              })}
            </p>
          </div>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="Display name" hint="Shown on action cards and recorded in the audit trail.">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              className={inputClass}
              placeholder="Your name"
            />
          </Field>

          <Field
            label="Email address"
            hint="Not editable: it identifies the account and is the subject of every OAuth grant, so changing it would orphan your connected integrations."
          >
            <input value={profile.email} readOnly disabled className={clsx(inputClass, 'opacity-60')} />
          </Field>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <Button
            variant="primary"
            icon="bi-check-lg"
            onClick={saveName}
            loading={savingName}
            disabled={name.trim() === (profile.name ?? '') || name.trim().length === 0}
          >
            Save name
          </Button>
          {name.trim() !== (profile.name ?? '') && (
            <Button variant="ghost" onClick={() => setName(profile.name ?? '')}>
              Reset
            </Button>
          )}
        </div>
      </GlassCard>

      {/* ── password */}
      <GlassCard className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
              <i className="bi bi-shield-lock mr-2 text-accent" aria-hidden />
              Password
            </h2>
            <p className="mt-1 text-xs text-ink-faint">
              {profile.hasPassword
                ? `Last changed ${
                    profile.passwordUpdatedAt
                      ? new Date(profile.passwordUpdatedAt).toLocaleDateString(undefined, {
                          day: 'numeric',
                          month: 'short',
                          year: 'numeric',
                        })
                      : 'at some point'
                  }.`
                : 'No password set yet for this account.'}
            </p>
          </div>
          <Badge tone={profile.hasPassword ? 'success' : 'warn'} icon={profile.hasPassword ? 'bi-check-lg' : 'bi-exclamation-triangle'}>
            {profile.hasPassword ? 'set' : 'not set'}
          </Badge>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {profile.hasPassword && (
            <Field
              label="Current password"
              required
              hint="Required even though you are signed in — that is what stops a borrowed laptop becoming a stolen account."
            >
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className={clsx(inputClass, 'font-mono')}
              />
            </Field>
          )}

          <Field
            label="New password"
            required
            hint={`At least ${profile.minPasswordLength} characters. A memorable phrase beats a short scramble.`}
          >
            <div className="relative">
              <input
                type={showNew ? 'text' : 'password'}
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className={clsx(inputClass, 'pr-10 font-mono')}
              />
              <button
                type="button"
                onClick={() => setShowNew((v) => !v)}
                aria-label={showNew ? 'Hide password' : 'Show password'}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-ink-faint hover:text-ink"
              >
                <i className={clsx('bi', showNew ? 'bi-eye-slash' : 'bi-eye')} aria-hidden />
              </button>
            </div>
          </Field>

          <Field label="Confirm new password" required>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className={clsx(inputClass, 'font-mono')}
            />
          </Field>
        </div>

        {newPassword.length > 0 && (
          <div className="mt-3">
            <div className="flex items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink/[0.07]">
                <div
                  className={clsx('h-full rounded-full transition-all', strength.bar)}
                  style={{ width: `${strength.pct}%` }}
                />
              </div>
              <span className={clsx('text-[11px] font-medium', strength.text)}>{strength.label}</span>
            </div>
            <p className="mt-1 text-[11px] text-ink-faint">{strength.hint}</p>
          </div>
        )}

        {passwordError && (
          <p className="mt-3 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
            {passwordError}
          </p>
        )}

        <div className="mt-4">
          <Button
            variant="primary"
            icon="bi-shield-lock-fill"
            onClick={savePassword}
            loading={savingPassword}
            disabled={
              newPassword.length === 0 ||
              confirmPassword.length === 0 ||
              (profile.hasPassword && currentPassword.length === 0)
            }
          >
            {profile.hasPassword ? 'Change password' : 'Set password'}
          </Button>
        </div>

        <p className="mt-3 text-[11px] text-ink-faint">
          <i className="bi bi-info-circle mr-1" aria-hidden />
          Hashed with scrypt (N=2¹⁵) and a per-account salt. The cost factor is versioned per row, so it
          can be raised later without invalidating anyone&apos;s password.
        </p>
      </GlassCard>

      {/* ── tour */}
      <GlassCard className="p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          <i className="bi bi-signpost-split mr-2 text-accent" aria-hidden />
          Product tour
        </h2>
        <p className="mt-1 text-xs text-ink-faint">
          {profile.onboardingCompletedAt
            ? profile.onboardingSkipped
              ? 'You skipped the walkthrough.'
              : 'You completed the walkthrough.'
            : 'The walkthrough has not been shown yet.'}
        </p>
        <Button
          variant="secondary"
          icon="bi-arrow-repeat"
          className="mt-3"
          onClick={async () => {
            await call('/api/profile/onboarding', 'POST', { action: 'restart' })
            toast.push({ tone: 'info', title: 'Tour reset', detail: 'Reload the page to see it again.' })
          }}
        >
          Show the tour again
        </Button>
      </GlassCard>

      {/* ── danger zone */}
      <GlassCard className="border-danger/40 p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-danger">
          <i className="bi bi-exclamation-octagon-fill mr-2" aria-hidden />
          Delete account
        </h2>

        {profile.deletionRequestedAt ? (
          <>
            <p className="mt-2 text-sm text-ink-muted">
              Deletion is scheduled. Your data will be permanently removed on{' '}
              <strong className="text-danger">
                {profile.deletionEffectiveAt
                  ? new Date(profile.deletionEffectiveAt).toLocaleString(undefined, {
                      day: 'numeric',
                      month: 'long',
                      year: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : 'shortly'}
              </strong>
              . You can still stop it.
            </p>
            <Button
              variant="success"
              icon="bi-arrow-counterclockwise"
              className="mt-4"
              onClick={cancelDeletion}
              loading={deleting}
            >
              Keep my account
            </Button>
          </>
        ) : (
          <>
            <p className="mt-2 max-w-2xl text-sm text-ink-muted">
              This removes your transcripts, action items, credentials, and integration connections.
              There is a <strong>7-day grace period</strong> before anything is actually deleted, and
              you can cancel at any point inside it.
            </p>
            <p className="mt-2 max-w-2xl text-xs text-ink-faint">
              The audit log is retained deliberately: deleting an account must not erase the record of
              what was executed in the world on its behalf. Those rows keep the event and lose the
              link to you.
            </p>

            <div className="mt-4 max-w-sm">
              <Field label={`Type ${profile.email} to confirm`} required>
                <input
                  value={deleteConfirm}
                  onChange={(e) => setDeleteConfirm(e.target.value)}
                  placeholder={profile.email}
                  autoComplete="off"
                  className={clsx(inputClass, 'font-mono')}
                />
              </Field>
            </div>

            <Button
              variant="danger"
              icon="bi-trash3-fill"
              className="mt-3"
              onClick={requestDeletion}
              loading={deleting}
              disabled={deleteConfirm.trim().toLowerCase() !== profile.email.toLowerCase()}
            >
              Schedule deletion
            </Button>
          </>
        )}
      </GlassCard>
    </div>
  )
}

/**
 * Advisory only — the server owns the actual policy. Scored on length first
 * because that is what genuinely resists cracking, with variety as a secondary
 * signal rather than a gate.
 */
function describeStrength(password: string, min: number) {
  const length = password.length
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length

  if (length === 0) return { pct: 0, label: '', bar: 'bg-ink-faint', text: 'text-ink-faint', hint: '' }
  if (length < min) {
    return {
      pct: Math.round((length / min) * 40),
      label: 'Too short',
      bar: 'bg-danger',
      text: 'text-danger',
      hint: `${min - length} more character${min - length === 1 ? '' : 's'} needed.`,
    }
  }
  if (length >= 20 || (length >= 16 && classes >= 3)) {
    return { pct: 100, label: 'Strong', bar: 'bg-ok', text: 'text-ok', hint: 'Long enough to resist offline cracking.' }
  }
  if (length >= min + 4 || classes >= 3) {
    return { pct: 70, label: 'Good', bar: 'bg-warn', text: 'text-warn', hint: 'A few more characters would help more than adding symbols.' }
  }
  return {
    pct: 50,
    label: 'Acceptable',
    bar: 'bg-warn',
    text: 'text-warn',
    hint: 'Length beats complexity — consider a longer passphrase.',
  }
}
