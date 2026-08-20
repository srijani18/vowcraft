'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { Badge, Button, EmptyState, GlassCard, Skeleton, inputClass } from '@/components/ui/primitives'
import { apiFetch } from '@/lib/api-client'
import type { TranscriptSummary } from '@/server/ingest/service'
import type { DecisionView, DecisionsResult } from '@/server/insights/service'

/**
 * Decisions & summary screen — SPEC-020.
 *
 * Read-only, like the audit log: a `Decision` row has no status and no correction path
 * (a re-extraction deletes and fully replaces a transcript's decisions), so there is
 * nothing here to approve, edit, or reject — only to search and cite back to its moment
 * in the meeting.
 */

function initialTranscriptId(): string {
  if (typeof window === 'undefined') return ''
  return new URLSearchParams(window.location.search).get('transcriptId') ?? ''
}

export function InsightsView({
  initial,
  recentSummaries,
}: {
  initial: DecisionsResult
  recentSummaries: TranscriptSummary[]
}) {
  const [data, setData] = useState(initial)
  const [q, setQ] = useState('')
  const [decidedBy, setDecidedBy] = useState('')
  const [transcriptId, setTranscriptId] = useState(initialTranscriptId)
  const [loading, setLoading] = useState(false)

  const search = (() => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (decidedBy) params.set('decidedBy', decidedBy)
    if (transcriptId) params.set('transcriptId', transcriptId)
    return params.toString()
  })()

  useEffect(() => {
    if (!q && !decidedBy && !transcriptId) return
    const timer = setTimeout(() => {
      setLoading(true)
      apiFetch(`/api/decisions?${search}`)
        .then((r) => r.json())
        .then((json: DecisionsResult) => setData(json))
        .finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(timer)
  }, [search, q, decidedBy, transcriptId])

  const loadMore = () => {
    if (!data.nextCursor) return
    setLoading(true)
    apiFetch(`/api/decisions?${search}${search ? '&' : ''}cursor=${data.nextCursor}`)
      .then((r) => r.json())
      .then((next: DecisionsResult) =>
        setData((prev) => ({ ...next, decisions: [...prev.decisions, ...next.decisions] })),
      )
      .finally(() => setLoading(false))
  }

  const clear = () => {
    setQ('')
    setDecidedBy('')
    setTranscriptId('')
    setData(initial)
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Decisions & summary</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            What every meeting settled, who made the call, and the moment it happened —
            searchable across every recording.
          </p>
        </div>
        <Badge tone="neutral" icon="bi-lightbulb-fill">
          {data.total.toLocaleString()} decisions
        </Badge>
      </header>

      {recentSummaries.length > 0 && (
        <GlassCard className="p-4">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
            Recent meeting summaries
          </p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {recentSummaries.map((t) => (
              <li key={t.id}>
                <Link
                  href={`/dashboard/transcripts/${t.id}`}
                  className="block rounded-lg border border-edge/20 bg-surface-strong/40 p-3 transition-colors hover:bg-ink/[0.04]"
                >
                  <p className="truncate text-sm font-medium">{t.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">{t.summary}</p>
                </Link>
              </li>
            ))}
          </ul>
        </GlassCard>
      )}

      <GlassCard className="lit-edge p-4">
        <div className="relative">
          <i
            className="bi bi-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-ink-faint"
            aria-hidden
          />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search decisions and quotes…"
            aria-label="Search decisions"
            className={clsx(inputClass, 'pl-8')}
          />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-edge/20 pt-3">
          <select
            value={transcriptId}
            onChange={(e) => setTranscriptId(e.target.value)}
            aria-label="Filter by meeting"
            className={clsx(inputClass, 'max-w-xs')}
          >
            <option value="">All meetings</option>
            {data.facets.transcripts.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>

          {data.facets.decidedBy.length > 0 && (
            <>
              <span className="mr-1 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
                Decided by
              </span>
              {data.facets.decidedBy.map((name) => {
                const on = decidedBy === name
                return (
                  <button
                    key={name}
                    onClick={() => setDecidedBy(on ? '' : name)}
                    aria-pressed={on}
                    className={clsx(
                      'rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors',
                      on
                        ? 'border-accent-fill bg-accent-fill/20 text-accent'
                        : 'border-edge/25 bg-surface-strong/50 text-ink-muted hover:text-ink',
                    )}
                  >
                    {name}
                  </button>
                )
              })}
            </>
          )}

          {(q || decidedBy || transcriptId) && (
            <Button variant="ghost" icon="bi-x-circle" onClick={clear}>
              Clear
            </Button>
          )}
        </div>
      </GlassCard>

      {loading && data.decisions.length === 0 && (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((n) => (
            <Skeleton key={n} className="h-20 w-full rounded-xl" />
          ))}
        </div>
      )}

      {!loading && data.decisions.length === 0 && (
        <GlassCard className="p-6">
          <EmptyState icon="bi-lightbulb" title="No matching decisions">
            Once a meeting is transcribed, anything it settles shows up here.
          </EmptyState>
        </GlassCard>
      )}

      <ol className="space-y-2">
        {data.decisions.map((decision) => (
          <DecisionRow key={decision.id} decision={decision} />
        ))}
      </ol>

      {data.nextCursor && (
        <div className="flex justify-center">
          <Button variant="secondary" icon="bi-arrow-down" onClick={loadMore} loading={loading}>
            Load older decisions
          </Button>
        </div>
      )}
    </div>
  )
}

function DecisionRow({ decision }: { decision: DecisionView }) {
  const at = new Date(decision.createdAt)
  return (
    <li className="panel rounded-xl px-4 py-3">
      <p className="text-sm font-medium">{decision.statement}</p>
      {decision.sourceQuote && (
        <blockquote className="mt-1.5 border-l-2 border-edge/30 pl-2.5 text-xs italic text-ink-muted">
          “{decision.sourceQuote}”
        </blockquote>
      )}
      <p className="mt-2 flex flex-wrap items-center gap-x-3 text-[11px] text-ink-faint">
        <span>
          <i className="bi bi-person mr-1" aria-hidden />
          {decision.decidedBy ?? 'Unattributed'}
        </span>
        <span>
          <i className="bi bi-clock mr-1" aria-hidden />
          {at.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
        </span>
        <Link
          href={`/dashboard/transcripts/${decision.transcript.id}${
            decision.sourceTimestampMs !== null ? `?t=${decision.sourceTimestampMs}` : ''
          }`}
          className="text-accent hover:underline"
        >
          <i className="bi bi-box-arrow-up-right mr-1" aria-hidden />
          {decision.transcript.title}
          {decision.sourceTimestampLabel && ` · ${decision.sourceTimestampLabel}`}
        </Link>
      </p>
    </li>
  )
}
