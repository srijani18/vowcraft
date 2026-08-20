'use client'

import clsx from 'clsx'
import { createContext, useCallback, useContext, useMemo, useState } from 'react'

/**
 * Minimal toast system. Every optimistic mutation needs somewhere to report a
 * rollback (SPEC-001 §11), and a failed approve that vanishes silently is worse
 * than no optimism at all.
 */

type ToastTone = 'success' | 'error' | 'info'

interface Toast {
  id: number
  tone: ToastTone
  title: string
  detail?: string
  href?: { label: string; url: string }
}

interface ToastApi {
  push(toast: Omit<Toast, 'id'>): void
}

const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}

const TONE: Record<ToastTone, { icon: string; ring: string }> = {
  success: { icon: 'bi-check-circle-fill text-risk-low', ring: 'border-risk-low/40' },
  error: { icon: 'bi-exclamation-triangle-fill text-risk-high', ring: 'border-risk-high/40' },
  info: { icon: 'bi-info-circle-fill text-accent', ring: 'border-accent/40' },
}

let nextId = 1

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = nextId++
    setToasts((prev) => [...prev, { ...toast, id }])
    // Errors linger: the user may need to read a provider message before it goes.
    const ttl = toast.tone === 'error' ? 9_000 : 5_000
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), ttl)
  }, [])

  const api = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
        role="status"
        aria-live="polite"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={clsx(
              'panel-strong pointer-events-auto animate-fade-up rounded-xl border p-3.5',
              TONE[toast.tone].ring,
            )}
          >
            <div className="flex items-start gap-2.5">
              <i className={clsx('bi mt-0.5', TONE[toast.tone].icon)} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{toast.title}</p>
                {toast.detail && <p className="mt-0.5 break-words text-xs text-ink-muted">{toast.detail}</p>}
                {toast.href && (
                  <a
                    href={toast.href.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
                  >
                    {toast.href.label} <i className="bi bi-box-arrow-up-right text-[10px]" aria-hidden />
                  </a>
                )}
              </div>
              <button
                onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
                className="rounded p-0.5 text-ink-faint hover:text-ink"
                aria-label="Dismiss"
              >
                <i className="bi bi-x-lg text-xs" aria-hidden />
              </button>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
