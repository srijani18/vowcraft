'use client'

import { useEffect, useState } from 'react'
import clsx from 'clsx'

/**
 * Three-state theme control: system → light → dark. "System" is a real state, not
 * an absence of one, so the toggle can hand control back to the OS rather than
 * trapping the user in whichever theme they tried once.
 *
 * The choice is written to `localStorage` and mirrored onto `data-theme`, which is
 * what `globals.css` keys off. The inline script in the root layout applies the
 * same value before first paint so there is no flash of the wrong theme.
 */

type Choice = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'voice2brd-theme'

const NEXT: Record<Choice, Choice> = { system: 'light', light: 'dark', dark: 'system' }

const META: Record<Choice, { icon: string; label: string }> = {
  system: { icon: 'bi-circle-half', label: 'Theme: follow system' },
  light: { icon: 'bi-brightness-high-fill', label: 'Theme: light' },
  dark: { icon: 'bi-moon-stars-fill', label: 'Theme: dark' },
}

function apply(choice: Choice) {
  const root = document.documentElement
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', choice)
}

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  // Start at 'system' and correct on mount: the server has no way to know the
  // stored choice, and rendering the wrong icon briefly is better than a
  // hydration mismatch.
  const [choice, setChoice] = useState<Choice>('system')
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) as Choice | null
    if (stored === 'light' || stored === 'dark' || stored === 'system') setChoice(stored)
    setMounted(true)
  }, [])

  const cycle = () => {
    const next = NEXT[choice]
    setChoice(next)
    apply(next)
    window.localStorage.setItem(STORAGE_KEY, next)
  }

  const meta = META[choice]

  return (
    <button
      onClick={cycle}
      title={meta.label}
      aria-label={meta.label}
      className={clsx(
        'inline-flex items-center gap-2 rounded-xl border border-edge/30 bg-surface-strong/70 text-ink-muted',
        'transition-colors hover:bg-surface-strong hover:text-ink',
        compact ? 'size-9 justify-center' : 'px-3 py-2 text-sm',
      )}
    >
      <i className={clsx('bi', meta.icon, !mounted && 'opacity-50')} aria-hidden />
      {!compact && <span className="capitalize">{choice}</span>}
    </button>
  )
}

/**
 * Runs before paint, inlined in <head>. Kept as a string rather than a module so
 * it executes synchronously — a deferred script would let the default theme paint
 * first and flash.
 */
export const THEME_INIT_SCRIPT = `
(function(){
  try {
    var c = localStorage.getItem('${STORAGE_KEY}');
    if (c === 'light' || c === 'dark') document.documentElement.setAttribute('data-theme', c);
  } catch (e) {}
})();
`
