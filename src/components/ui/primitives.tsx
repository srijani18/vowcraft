import clsx from 'clsx'
import type { ReactNode } from 'react'

/** Shared surface + control primitives for the panel design system. */

export function GlassCard({
  children,
  className,
  strong,
  lit = true,
  interactive,
  as: Tag = 'div',
}: {
  children: ReactNode
  className?: string
  strong?: boolean
  lit?: boolean
  /** Lights up on hover. Use for cards that are themselves a target. */
  interactive?: boolean
  as?: 'div' | 'section' | 'article' | 'aside'
}) {
  return (
    <Tag
      className={clsx(
        strong ? 'panel-strong' : 'panel',
        lit && 'lit-edge',
        interactive && 'panel-interactive',
        'rounded-2xl',
        className,
      )}
    >
      {children}
    </Tag>
  )
}

type Tone = 'neutral' | 'accent' | 'cta' | 'success' | 'warn' | 'danger' | 'muted'

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'border-edge/25 bg-surface-strong/60 text-ink-muted',
  accent: 'border-accent/40 bg-accent-fill/12 text-accent',
  cta: 'border-cta/50 bg-cta/15 text-warn',
  success: 'border-ok/45 bg-ok/12 text-ok',
  warn: 'border-warn/45 bg-warn/12 text-warn',
  danger: 'border-danger/45 bg-danger/12 text-danger',
  muted: 'border-edge/15 bg-ink-faint/10 text-ink-faint',
}

export function Badge({
  children,
  tone = 'neutral',
  icon,
  title,
  className,
}: {
  children: ReactNode
  tone?: Tone
  icon?: string
  title?: string
  className?: string
}) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5',
        TONE_CLASS[tone],
        className,
      )}
    >
      {icon && <i className={clsx('bi', icon, 'text-[11px]')} aria-hidden />}
      {children}
    </span>
  )
}

type Variant = 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger' | 'success'

/*
 * The palette assigns teal to buttons and links, and orange to CTAs. Split that
 * way: `accent` is the interactive teal (links, secondary actions, connect), and
 * `primary` is the orange reserved for the consequential action on a screen —
 * Execute, Save, Create account. Two fills rather than one, because a page where
 * everything is the CTA colour has no CTA.
 *
 * Both fills carry `#0E2126` labels: 5.4:1 on teal and 7.8:1 on orange. Neither
 * accent may be text on a pale ground (2.8:1 and 1.9:1) — that is what `accent`
 * as a *colour token* is for.
 */
const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'border-cta-edge bg-cta text-cta-on font-semibold glow-cta hover:brightness-105 active:brightness-95',
  accent:
    'border-accent-edge bg-accent-fill text-accent-on font-semibold glow-accent hover:brightness-105 active:brightness-95',
  secondary: 'border-edge/30 bg-surface-strong/70 text-ink hover:bg-surface-strong hover:border-accent-fill/50',
  ghost: 'border-transparent bg-transparent text-ink-muted hover:bg-ink/5 hover:text-ink',
  danger: 'border-danger/50 bg-danger/15 text-danger hover:bg-danger/25',
  success: 'border-ok/50 bg-ok/15 text-ok hover:bg-ok/25',
}

export function Button({
  children,
  variant = 'secondary',
  icon,
  loading,
  className,
  type = 'button',
  ...rest
}: {
  children?: ReactNode
  variant?: Variant
  icon?: string
  loading?: boolean
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type={type}
      // A loading button must also be disabled, or a second click double-submits.
      disabled={rest.disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium',
        'transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:shadow-none',
        VARIANT_CLASS[variant],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <i className="bi bi-arrow-repeat animate-spin text-sm" aria-hidden />
      ) : (
        icon && <i className={clsx('bi', icon)} aria-hidden />
      )}
      {children}
    </button>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={clsx('relative overflow-hidden rounded-lg bg-ink/[0.07]', className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-ink/10 to-transparent" />
    </div>
  )
}

export function EmptyState({
  icon = 'bi-inbox',
  title,
  children,
}: {
  icon?: string
  title: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-edge/30 px-6 py-10 text-center">
      <i className={clsx('bi', icon, 'text-2xl text-ink-faint')} aria-hidden />
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {children && <p className="max-w-sm text-xs text-ink-faint">{children}</p>}
    </div>
  )
}

export function Field({
  label,
  hint,
  error,
  children,
  required,
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  required?: boolean
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-baseline gap-1.5 text-xs font-medium text-ink-muted">
        {label}
        {required && <span className="text-risk-high" aria-label="required">*</span>}
      </span>
      {children}
      {error ? (
        <span className="mt-1 block text-xs text-risk-high">{error}</span>
      ) : (
        hint && <span className="mt-1 block text-xs text-ink-faint">{hint}</span>
      )}
    </label>
  )
}

export const inputClass =
  'w-full rounded-xl border border-edge/30 bg-base px-3 py-2 text-sm text-ink placeholder:text-ink-faint ' +
  'focus-visible:border-accent-fill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-fill/40'
