'use client'

import { useEffect } from 'react'

/** Root error boundary — SPEC-001 §11 requires a retry path, not a dead end. */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error(JSON.stringify({ level: 'error', event: 'ui.boundary', message: error.message, digest: error.digest }))
  }, [error])

  return (
    <main className="grid min-h-dvh place-items-center p-6">
      <div className="panel lit-edge w-full max-w-md rounded-2xl p-8 text-center">
        <i className="bi bi-exclamation-octagon-fill text-3xl text-risk-high" aria-hidden />
        <h1 className="mt-4 text-lg font-semibold">Something broke on this page</h1>
        <p className="mt-2 text-sm text-ink-muted">{error.message}</p>
        {error.digest && <p className="mono-num mt-3 text-ink-faint">digest {error.digest}</p>}
        <button
          onClick={reset}
          className="mt-6 inline-flex items-center gap-2 rounded-xl border border-edge bg-surface-strong px-4 py-2 text-sm font-medium hover:bg-white/10"
        >
          <i className="bi bi-arrow-clockwise" aria-hidden /> Try again
        </button>
      </div>
    </main>
  )
}
