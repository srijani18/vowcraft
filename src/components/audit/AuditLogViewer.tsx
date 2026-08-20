'use client'

import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { Badge, Button, EmptyState, GlassCard, Skeleton, inputClass } from '@/components/ui/primitives'
import { apiFetch } from '@/lib/api-client'
import type { AuditEntryView, AuditResult } from '@/server/analytics/service'

/**
 * Audit log screen — SPEC-003 §7.
 *
 * Read-only by construction: the API exposes no write verb for this table, and
 * neither does this component. The expandable row shows the raw before/after and
 * metadata JSON rather than a prettified summary — when someone opens an audit
 * log they are usually reconciling a dispute, and a paraphrase is not evidence.
 */

const EVENT_META: Record<string, { icon: string; tone: 'accent' | 'success' | 'warn' | 'danger' | 'muted' | 'neutral'; label: string }> = {
  'action_item.created': { icon: 'bi-plus-circle', tone: 'muted', label: 'Created' },
  'action_item.edited': { icon: 'bi-pencil', tone: 'neutral', label: 'Edited' },
  'action_item.approved': { icon: 'bi-check-lg', tone: 'success', label: 'Approved' },
  'action_item.rejected': { icon: 'bi-x-lg', tone: 'muted', label: 'Rejected' },
  'action_item.deferred': { icon: 'bi-clock-history', tone: 'warn', label: 'Deferred' },
  'action_item.reopened': { icon: 'bi-arrow-counterclockwise', tone: 'neutral', label: 'Reopened' },
  'action_item.execution_requested': { icon: 'bi-send', tone: 'accent', label: 'Execution requested' },
  'action_item.executed': { icon: 'bi-check-circle-fill', tone: 'success', label: 'Executed' },
  'action_item.execution_failed': { icon: 'bi-x-octagon-fill', tone: 'danger', label: 'Execution failed' },
  'action_item.guardrail_blocked': { icon: 'bi-slash-circle-fill', tone: 'danger', label: 'Guardrail blocked' },
  'integration.connected': { icon: 'bi-plug-fill', tone: 'success', label: 'Integration connected' },
  'integration.reauth_required': { icon: 'bi-exclamation-triangle-fill', tone: 'warn', label: 'Reauth required' },
  'credential.saved': { icon: 'bi-key-fill', tone: 'accent', label: 'Key saved' },
  'credential.deleted': { icon: 'bi-trash', tone: 'muted', label: 'Key deleted' },
  'credential.verified': { icon: 'bi-patch-check-fill', tone: 'success', label: 'Key verified' },
  'profile.updated': { icon: 'bi-person-gear', tone: 'neutral', label: 'Profile updated' },
  'profile.password_changed': { icon: 'bi-shield-lock-fill', tone: 'accent', label: 'Password changed' },
  'profile.onboarding': { icon: 'bi-signpost-split', tone: 'muted', label: 'Onboarding' },
  'profile.deletion_requested': { icon: 'bi-exclamation-octagon-fill', tone: 'danger', label: 'Deletion requested' },
  'profile.deletion_cancelled': { icon: 'bi-arrow-counterclockwise', tone: 'success', label: 'Deletion cancelled' },
  'settings.updated': { icon: 'bi-sliders', tone: 'neutral', label: 'Settings updated' },
  'transcript.uploaded': { icon: 'bi-cloud-arrow-up-fill', tone: 'accent', label: 'Recording uploaded' },
  'transcript.extracted': { icon: 'bi-magic', tone: 'success', label: 'Actions extracted' },
  'transcript.extraction_failed': {
    icon: 'bi-exclamation-diamond-fill',
    tone: 'danger',
    label: 'Extraction failed',
  },
  'transcript.deleted': { icon: 'bi-trash', tone: 'muted', label: 'Recording deleted' },
  'brd.created': { icon: 'bi-file-earmark-plus-fill', tone: 'accent', label: 'BRD created' },
  'brd.revised': { icon: 'bi-arrow-repeat', tone: 'success', label: 'BRD revised' },
  'brd.renamed': { icon: 'bi-pencil', tone: 'neutral', label: 'BRD renamed' },
  'brd.deleted': { icon: 'bi-trash', tone: 'muted', label: 'BRD deleted' },
  'brd.generation_failed': { icon: 'bi-exclamation-diamond-fill', tone: 'danger', label: 'BRD generation failed' },
}

function meta(event: string) {
  return EVENT_META[event] ?? { icon: 'bi-dot', tone: 'neutral' as const, label: event }
}

export function AuditLogViewer({ initial }: { initial: AuditResult }) {
  const [data, setData] = useState(initial)
  const [events, setEvents] = useState<string[]>([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)

  const search = (() => {
    const params = new URLSearchParams()
    for (const e of events) params.append('event', e)
    if (q) params.set('q', q)
    return params.toString()
  })()

  useEffect(() => {
    if (events.length === 0 && !q) return
    const timer = setTimeout(() => {
      setLoading(true)
      apiFetch(`/api/audit-log?${search}`)
        .then((r) => r.json())
        .then((json: AuditResult) => setData(json))
        .finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(timer)
  }, [search, events.length, q])

  const toggleEvent = (event: string) =>
    setEvents((prev) => (prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]))

  const loadMore = () => {
    if (!data.nextCursor) return
    setLoading(true)
    apiFetch(`/api/audit-log?${search}${search ? '&' : ''}cursor=${data.nextCursor}`)
      .then((r) => r.json())
      .then((next: AuditResult) =>
        setData((prev) => ({ ...next, entries: [...prev.entries, ...next.entries] })),
      )
      .finally(() => setLoading(false))
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Audit log</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            Every proposal, decision, and execution, in order. Append-only — no part of this
            application can modify or delete a row here.
          </p>
        </div>
        <Badge tone="neutral" icon="bi-lock-fill">
          {data.total.toLocaleString()} events · immutable
        </Badge>
      </header>

      <GlassCard className="lit-edge p-4">
        <div className="relative">
          <i
            className="bi bi-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-ink-faint"
            aria-hidden
          />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search events and action descriptions…"
            aria-label="Search the audit log"
            className={clsx(inputClass, 'pl-8')}
          />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-edge/20 pt-3">
          <span className="mr-1 text-[11px] font-medium uppercase tracking-wide text-ink-faint">Event</span>
          {data.facets.events.map((facet) => {
            const on = events.includes(facet.event)
            return (
              <button
                key={facet.event}
                onClick={() => toggleEvent(facet.event)}
                aria-pressed={on}
                className={clsx(
                  'rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors',
                  on
                    ? 'border-accent-fill bg-accent-fill/20 text-accent'
                    : 'border-edge/25 bg-surface-strong/50 text-ink-muted hover:text-ink',
                )}
              >
                {meta(facet.event).label}
                <span className="mono-num ml-1.5 opacity-60">{facet.count}</span>
              </button>
            )
          })}
          {(events.length > 0 || q) && (
            <Button
              variant="ghost"
              icon="bi-x-circle"
              onClick={() => {
                setEvents([])
                setQ('')
                setData(initial)
              }}
            >
              Clear
            </Button>
          )}
        </div>
      </GlassCard>

      {loading && data.entries.length === 0 && (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((n) => (
            <Skeleton key={n} className="h-16 w-full rounded-xl" />
          ))}
        </div>
      )}

      {!loading && data.entries.length === 0 && (
        <GlassCard className="p-6">
          <EmptyState icon="bi-journal" title="No matching events">
            Approve or execute an action item and it will appear here immediately.
          </EmptyState>
        </GlassCard>
      )}

      <ol className="space-y-2">
        {data.entries.map((entry) => (
          <AuditRow
            key={entry.id}
            entry={entry}
            open={expanded === entry.id}
            onToggle={() => setExpanded(expanded === entry.id ? null : entry.id)}
          />
        ))}
      </ol>

      {data.nextCursor && (
        <div className="flex justify-center">
          <Button variant="secondary" icon="bi-arrow-down" onClick={loadMore} loading={loading}>
            Load older events
          </Button>
        </div>
      )}
    </div>
  )
}

function AuditRow({
  entry,
  open,
  onToggle,
}: {
  entry: AuditEntryView
  open: boolean
  onToggle(): void
}) {
  const m = meta(entry.event)
  const hasDetail = Boolean(entry.before || entry.after || entry.metadata)
  const at = new Date(entry.at)

  return (
    <li className="panel overflow-hidden rounded-xl">
      <button
        onClick={onToggle}
        aria-expanded={open}
        disabled={!hasDetail}
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors enabled:hover:bg-ink/[0.04]"
      >
        <i className={clsx('bi', m.icon, 'mt-0.5 shrink-0', toneText(m.tone))} aria-hidden />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-sm font-medium">{m.label}</span>
            <span className="mono-num text-ink-faint">{entry.event}</span>
          </div>
          {entry.actionItemDescription && (
            <p className="mt-0.5 truncate text-xs text-ink-muted">{entry.actionItemDescription}</p>
          )}
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px] text-ink-faint">
            <span>
              <i className="bi bi-person mr-1" aria-hidden />
              {entry.actorLabel}
            </span>
            <span>
              <i className="bi bi-clock mr-1" aria-hidden />
              {at.toLocaleString(undefined, {
                day: 'numeric',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
              })}
            </span>
            {entry.requestId && <span className="mono-num">{entry.requestId}</span>}
          </p>
        </div>

        {hasDetail && (
          <i
            className={clsx('bi bi-chevron-down mt-1 shrink-0 text-xs text-ink-faint transition-transform', open && 'rotate-180')}
            aria-hidden
          />
        )}
      </button>

      {open && hasDetail && (
        <div className="grid gap-3 border-t border-edge/20 bg-base/40 px-4 py-3 sm:grid-cols-2">
          {entry.before !== null && entry.before !== undefined && (
            <Json label="Before" value={entry.before} />
          )}
          {entry.after !== null && entry.after !== undefined && <Json label="After" value={entry.after} />}
          {entry.metadata !== null && entry.metadata !== undefined && (
            <div className="sm:col-span-2">
              <Json label="Context" value={entry.metadata} />
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function toneText(tone: string): string {
  return tone === 'success'
    ? 'text-ok'
    : tone === 'danger'
      ? 'text-danger'
      : tone === 'warn'
        ? 'text-warn'
        : tone === 'accent'
          ? 'text-accent'
          : 'text-ink-faint'
}

function Json({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{label}</p>
      <pre className="overflow-x-auto rounded-lg border border-edge/20 bg-base p-2.5 text-[11px] leading-relaxed text-ink-muted">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  )
}
