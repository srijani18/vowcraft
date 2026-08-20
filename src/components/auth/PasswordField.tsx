'use client'

import clsx from 'clsx'
import { useId, useState } from 'react'
import { scorePassword } from '@/lib/password-rules'

/**
 * Password input with a show/hide toggle and an optional strength meter.
 *
 * Details that matter for this control specifically:
 *  - The toggle is a real `<button>` with `aria-pressed`, not a styled span, and
 *    it is outside the input so it never sits on top of a browser's own reveal.
 *  - Revealing resets to hidden on blur is *not* done: someone who reveals a
 *    password is usually about to check a typo, and re-hiding it mid-check is
 *    hostile. It hides on submit instead.
 *  - `autoComplete` is set correctly per mode, because fighting the password
 *    manager makes people choose worse passwords.
 */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  error,
  autoComplete,
  showMeter,
  hint,
  revealed,
  onRevealChange,
}: {
  id?: string
  label: string
  value: string
  onChange(value: string): void
  error?: string
  autoComplete: 'new-password' | 'current-password'
  showMeter?: boolean
  hint?: string
  /** Lifted so both fields on the signup form reveal together. */
  revealed?: boolean
  onRevealChange?(revealed: boolean): void
}) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const [localRevealed, setLocalRevealed] = useState(false)
  const isRevealed = revealed ?? localRevealed
  const setRevealed = onRevealChange ?? setLocalRevealed

  const strength = scorePassword(value)
  const describedBy = [error ? `${inputId}-error` : null, hint && !error ? `${inputId}-hint` : null]
    .filter(Boolean)
    .join(' ')

  const BAR = ['bg-danger', 'bg-warn', 'bg-warn', 'bg-ok'] as const
  const TEXT = ['text-danger', 'text-warn', 'text-warn', 'text-ok'] as const

  return (
    <div>
      <label htmlFor={inputId} className="mb-1.5 block text-xs font-medium text-ink-muted">
        {label}
      </label>

      <div className="flex gap-2">
        <input
          id={inputId}
          type={isRevealed ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          spellCheck={false}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          className={clsx(
            'min-w-0 flex-1 rounded-xl border bg-base px-3 py-2.5 font-mono text-sm text-ink',
            'placeholder:text-ink-faint focus-visible:outline-none focus-visible:ring-2',
            error
              ? 'border-danger/60 focus-visible:border-danger focus-visible:ring-danger/30'
              : 'border-edge/30 focus-visible:border-accent-fill focus-visible:ring-accent-fill/40',
          )}
        />

        <button
          type="button"
          onClick={() => setRevealed(!isRevealed)}
          aria-pressed={isRevealed}
          aria-label={isRevealed ? 'Hide password' : 'Show password'}
          title={isRevealed ? 'Hide password' : 'Show password'}
          className="grid size-[42px] shrink-0 place-items-center rounded-xl border border-edge/30 bg-surface-strong/70 text-ink-muted transition-colors hover:border-accent-fill/40 hover:text-ink"
        >
          <i className={clsx('bi', isRevealed ? 'bi-eye-slash' : 'bi-eye')} aria-hidden />
        </button>
      </div>

      {showMeter && value.length > 0 && (
        <div className="mt-2">
          <div className="flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
              <div
                className={clsx('h-full rounded-full transition-all duration-300', BAR[strength.score])}
                style={{ width: `${strength.pct}%` }}
              />
            </div>
            <span className={clsx('text-[11px] font-medium', TEXT[strength.score])}>{strength.label}</span>
          </div>
          {strength.hint && <p className="mt-1 text-[11px] text-ink-faint">{strength.hint}</p>}
        </div>
      )}

      {error ? (
        <p id={`${inputId}-error`} role="alert" className="mt-1.5 flex items-start gap-1.5 text-xs text-danger">
          <i className="bi bi-exclamation-circle-fill mt-0.5 shrink-0" aria-hidden />
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${inputId}-hint`} className="mt-1.5 text-[11px] text-ink-faint">
            {hint}
          </p>
        )
      )}
    </div>
  )
}
