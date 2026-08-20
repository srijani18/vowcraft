'use client'

import clsx from 'clsx'
import { useEffect, useRef } from 'react'

/**
 * Dialog built on <dialog> so focus trapping, Escape, and the backdrop come from
 * the platform rather than from hand-rolled key handlers.
 */
export function Modal({
  open,
  onClose,
  title,
  subtitle,
  icon,
  children,
  footer,
  width = 'max-w-lg',
}: {
  open: boolean
  onClose(): void
  title: string
  subtitle?: string
  icon?: string
  children: React.ReactNode
  footer?: React.ReactNode
  width?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    // Fires for Escape and for form method=dialog alike, so state stays in sync.
    const handleClose = () => onClose()
    dialog.addEventListener('close', handleClose)
    return () => dialog.removeEventListener('close', handleClose)
  }, [onClose])

  return (
    <dialog
      ref={ref}
      // Clicking the backdrop (the dialog element itself) closes; clicks inside
      // the panel stop propagating.
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
      className={clsx(
        'w-[calc(100vw-2rem)] rounded-2xl border border-edge bg-surface-strong p-0 text-ink shadow-panel',
        'backdrop:bg-abyss/70 backdrop:backdrop-blur-sm open:animate-fade-up',
        width,
      )}
      aria-labelledby="modal-title"
    >
      <div className="lit-edge flex items-start gap-3 border-b border-edge px-5 py-4">
        {icon && <i className={clsx('bi', icon, 'mt-0.5 text-lg text-accent')} aria-hidden />}
        <div className="min-w-0 flex-1">
          <h2 id="modal-title" className="text-base font-semibold">
            {title}
          </h2>
          {subtitle && <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>}
        </div>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-ink-faint hover:bg-white/5 hover:text-ink"
          aria-label="Close"
        >
          <i className="bi bi-x-lg text-sm" aria-hidden />
        </button>
      </div>

      <div className="max-h-[65dvh] overflow-y-auto px-5 py-4">{children}</div>

      {footer && (
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-edge bg-abyss/[0.05] dark:bg-abyss/30 px-5 py-3.5">
          {footer}
        </div>
      )}
    </dialog>
  )
}
