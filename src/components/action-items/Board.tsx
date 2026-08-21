'use client'

import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState, useTransition } from 'react'
import { Badge, Button, EmptyState, GlassCard, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { GROUP_META, GROUP_ORDER } from '@/domain/action-item'
import type { Readiness } from '@/domain/types'
import type { ActionItemDTO } from '@/server/action-items/dto'
import type { ListResult } from '@/server/action-items/service'
import {
  ApiError,
  bulkOp,
  executeItem,
  fetchList,
  getWorkflow,
  patchItem,
  runWorkflow,
  type ExecuteOutcome,
  type WorkflowOutcome,
  type WorkflowPreview,
} from './api'
import { ActionItemCard } from './ActionItemCard'
import { EditModal } from './EditModal'
import { ExecuteModal } from './ExecuteModal'
import { RunWorkflowModal } from './RunWorkflowModal'
import {
  EMPTY_FILTERS,
  Filters,
  filtersToSearch,
  searchToFilters,
  useSearchHotkey,
  type FilterState,
} from './Filters'

/**
 * The dashboard's client half. Owns filter state, optimistic decisions, and the
 * two modals.
 *
 * Decisions are optimistic and roll back with a toast; **execution never is**
 * (SPEC-001 §11). An approve that silently failed is recoverable; a card that
 * claims a calendar invite went out when it did not is a lie the reviewer will
 * act on.
 */

export function Board({ initial, initialSearch }: { initial: ListResult; initialSearch: string }) {
  const toast = useToast()
  const [, startTransition] = useTransition()

  const [data, setData] = useState<ListResult>(initial)
  const [filters, setFilters] = useState<FilterState>(() =>
    searchToFilters(new URLSearchParams(initialSearch)),
  )
  const [loading, setLoading] = useState(false)
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())
  const [focusedId, setFocusedId] = useState<string | null>(null)

  const [editing, setEditing] = useState<ActionItemDTO | null>(null)
  const [saving, setSaving] = useState(false)

  const [executeTarget, setExecuteTarget] = useState<ActionItemDTO | null>(null)
  const [preview, setPreview] = useState<ExecuteOutcome | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [executeError, setExecuteError] = useState<string | null>(null)

  const [workflowTarget, setWorkflowTarget] = useState<ActionItemDTO | null>(null)
  const [workflowPreview, setWorkflowPreview] = useState<WorkflowPreview | null>(null)
  const [loadingWorkflowPreview, setLoadingWorkflowPreview] = useState(false)
  const [runningWorkflow, setRunningWorkflow] = useState(false)
  const [workflowResult, setWorkflowResult] = useState<WorkflowOutcome | null>(null)

  const searchRef = useRef<HTMLInputElement>(null)
  useSearchHotkey(searchRef)

  const markBusy = (id: string, busy: boolean) =>
    setBusyIds((prev) => {
      const next = new Set(prev)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })

  // ── filters → URL → refetch, debounced so typing does not hammer the API
  const search = filtersToSearch(filters)
  const lastSearch = useRef(initialSearch)

  useEffect(() => {
    if (search === lastSearch.current) return
    const timer = setTimeout(() => {
      lastSearch.current = search
      const url = search ? `?${search}` : window.location.pathname
      window.history.replaceState(null, '', url)
      setLoading(true)
      fetchList(search)
        .then(setData)
        .catch((err: unknown) =>
          toast.push({ tone: 'error', title: 'Could not load action items', detail: (err as Error).message }),
        )
        .finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(timer)
  }, [search, toast])

  const refresh = useCallback(() => {
    fetchList(lastSearch.current)
      .then(setData)
      .catch(() => {
        /* a failed background refresh must not clobber the visible list */
      })
  }, [])

  const replaceItem = (updated: ActionItemDTO) =>
    setData((prev) => ({ ...prev, items: prev.items.map((i) => (i.id === updated.id ? updated : i)) }))

  // ── decisions (optimistic)
  const decide = async (item: ActionItemDTO, status: 'APPROVED' | 'REJECTED' | 'DEFERRED' | 'PROPOSED') => {
    const snapshot = item
    markBusy(item.id, true)
    replaceItem({ ...item, status })

    try {
      const updated = await patchItem(item.id, { status })
      replaceItem(updated)
    } catch (err) {
      replaceItem(snapshot) // roll back to exactly what was there
      const e = err as ApiError
      toast.push({
        tone: 'error',
        title: `Could not ${status.toLowerCase()} this action`,
        detail: e.message,
      })
    } finally {
      markBusy(item.id, false)
    }
  }

  const saveEdit = async (patch: Record<string, unknown>) => {
    if (!editing) return
    setSaving(true)
    try {
      const updated = await patchItem(editing.id, patch)
      replaceItem(updated)
      setEditing(null)
      toast.push({ tone: 'success', title: 'Action item updated', detail: 'Saved as a correction for future extractions.' })
    } catch (err) {
      const e = err as ApiError
      toast.push({ tone: 'error', title: 'Could not save changes', detail: e.message })
    } finally {
      setSaving(false)
    }
  }

  // ── execution: always a server dry run first, then an explicit confirm
  const openExecute = async (item: ActionItemDTO) => {
    setExecuteTarget(item)
    setPreview(null)
    setExecuteError(null)
    setLoadingPreview(true)
    try {
      setPreview(await executeItem(item.id, { dryRun: true }))
    } catch (err) {
      const e = err as ApiError
      setExecuteError(e.message)
    } finally {
      setLoadingPreview(false)
    }
  }

  const confirmExecute = async (acknowledgedWarnings: string[]) => {
    if (!executeTarget) return
    setExecuting(true)
    setExecuteError(null)
    try {
      // `confirmExecute` is only ever called after the reviewer has clicked through
      // ExecuteModal's confirmation step, so that click itself is what `confirmed`
      // reports — the backend's stricter EXPLICIT_APPROVAL_WITH_CONFIRMATION gate reads
      // it (see app/domain/risk.py::gate_satisfied on the FastAPI side).
      const outcome = await executeItem(executeTarget.id, { acknowledgedWarnings, confirmed: true })
      toast.push({
        tone: 'success',
        title: outcome.replayed ? 'Already executed' : 'Executed',
        detail: outcome.result?.summary,
        ...(outcome.result?.externalUrl
          ? { href: { label: 'Open it', url: outcome.result.externalUrl } }
          : {}),
      })
      setExecuteTarget(null)
      startTransition(refresh)
    } catch (err) {
      const e = err as ApiError
      setExecuteError(e.message)
      toast.push({ tone: 'error', title: 'Execution failed', detail: e.message })
      // The row's status changed to FAILED server-side; reflect that.
      startTransition(refresh)
    } finally {
      setExecuting(false)
    }
  }

  // ── workflow orchestrator (SPEC-002 §8): preview the chain, then run it on confirm.
  const openWorkflow = async (item: ActionItemDTO) => {
    setWorkflowTarget(item)
    setWorkflowPreview(null)
    setWorkflowResult(null)
    setLoadingWorkflowPreview(true)
    try {
      setWorkflowPreview(await getWorkflow(item.id))
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not preview the workflow', detail: (err as Error).message })
      setWorkflowTarget(null)
    } finally {
      setLoadingWorkflowPreview(false)
    }
  }

  const confirmWorkflow = async () => {
    if (!workflowTarget) return
    setRunningWorkflow(true)
    try {
      const outcome = await runWorkflow(workflowTarget.id, { confirmed: true })
      setWorkflowResult(outcome)
      toast.push({
        tone: outcome.ok ? 'success' : 'info',
        title: outcome.ok ? `${outcome.steps.length} steps executed` : 'Workflow stopped early',
        detail: outcome.haltedAt?.message,
      })
      startTransition(refresh)
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not run the workflow', detail: (err as Error).message })
      startTransition(refresh)
    } finally {
      setRunningWorkflow(false)
    }
  }

  // ── bulk
  const runBulk = async (ids: string[], op: 'approve' | 'reject' | 'defer') => {
    if (ids.length === 0) return
    try {
      const result = await bulkOp(ids, op)
      toast.push({
        tone: result.failedCount > 0 ? 'info' : 'success',
        title: `${result.okCount} ${op === 'approve' ? 'approved' : op === 'reject' ? 'rejected' : 'deferred'}`,
        detail:
          result.failedCount > 0
            ? `${result.failedCount} could not change: ${result.results.find((r) => !r.ok)?.error ?? ''}`
            : undefined,
      })
      refresh()
    } catch (err) {
      toast.push({ tone: 'error', title: 'Bulk update failed', detail: (err as Error).message })
    }
  }

  // ── keyboard shortcuts on the focused card (SPEC-001 §11)
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.tagName === 'SELECT'
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return

      const item = data.items.find((i) => i.id === focusedId)
      if (!item) return

      const key = event.key.toLowerCase()
      if (key === 'a' && item.status !== 'APPROVED') {
        event.preventDefault()
        void decide(item, 'APPROVED')
      } else if (key === 'r' && item.status !== 'REJECTED') {
        event.preventDefault()
        void decide(item, 'REJECTED')
      } else if (key === 'd' && item.status !== 'DEFERRED') {
        event.preventDefault()
        void decide(item, 'DEFERRED')
      } else if (key === 'e' && item.canExecute) {
        event.preventDefault()
        void openExecute(item)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- decide/openExecute are stable enough for a key handler
  }, [data.items, focusedId])

  const groups = GROUP_ORDER.map((readiness) => ({
    readiness,
    items: data.items.filter((i) => i.readiness === readiness),
  }))

  const readyApproved = data.items.filter((i) => i.status === 'APPROVED' && i.canExecute)

  return (
    <>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Action items</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {data.counts.total} extracted from your meetings. Approve what is right, fix what is not.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="success" icon="bi-lightning-charge-fill">
            {data.counts.READY} ready
          </Badge>
          <Badge tone="warn" icon="bi-question-circle">
            {data.counts.NEEDS_CLARIFICATION} to clarify
          </Badge>
          <Badge tone="muted" icon="bi-info-circle">
            {data.counts.INFORMATIONAL} informational
          </Badge>
        </div>
      </header>

      <Filters
        filters={filters}
        owners={data.facets.owners}
        transcripts={data.facets.transcripts}
        onChange={setFilters}
        onClear={() => setFilters(EMPTY_FILTERS)}
        searchRef={searchRef}
      />

      {loading && (
        <div className="mb-4 space-y-3">
          {[0, 1, 2].map((n) => (
            <Skeleton key={n} className="h-32 w-full rounded-2xl" />
          ))}
        </div>
      )}

      {!loading && data.items.length === 0 && (
        <GlassCard className="p-6">
          <EmptyState icon="bi-inbox" title="Nothing matches these filters">
            Clear a filter, or upload a recording to extract new action items.
          </EmptyState>
        </GlassCard>
      )}

      {!loading && (
        <div className="space-y-8">
          {groups.map(({ readiness, items }) => {
            if (items.length === 0) return null
            const meta = GROUP_META[readiness]
            const pending = items.filter((i) => i.status === 'PROPOSED')

            return (
              <section key={readiness} aria-labelledby={`group-${readiness}`}>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-baseline gap-2.5">
                    <h2
                      id={`group-${readiness}`}
                      className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-ink-muted"
                    >
                      <i className={clsx('bi', meta.icon, toneFor(readiness))} aria-hidden />
                      {meta.title}
                      <span className="mono-num rounded-full border border-edge px-1.5 py-0.5 text-ink-faint">
                        {items.length}
                      </span>
                    </h2>
                    <p className="hidden text-xs text-ink-faint sm:block">{meta.blurb}</p>
                  </div>

                  <div className="flex items-center gap-2">
                    {readiness === 'READY' && pending.length > 0 && (
                      <Button
                        variant="success"
                        icon="bi-check-all"
                        onClick={() => runBulk(pending.map((i) => i.id), 'approve')}
                      >
                        Approve all {pending.length}
                      </Button>
                    )}
                    {readiness === 'READY' && readyApproved.length > 0 && (
                      <span className="text-xs text-ink-faint">
                        {readyApproved.length} approved and awaiting execution
                      </span>
                    )}
                  </div>
                </div>

                <div className="grid gap-3">
                  {items.map((item) => (
                    <ActionItemCard
                      key={item.id}
                      item={item}
                      busy={busyIds.has(item.id)}
                      focused={focusedId === item.id}
                      onFocus={() => setFocusedId(item.id)}
                      onDecide={(status) => void decide(item, status)}
                      onEdit={() => setEditing(item)}
                      onExecute={() => void openExecute(item)}
                      onRunWorkflow={() => void openWorkflow(item)}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      )}

      {data.nextCursor && (
        <div className="mt-6 flex justify-center">
          <Button
            variant="secondary"
            icon="bi-arrow-down"
            onClick={() =>
              fetchList(`${search}${search ? '&' : ''}cursor=${data.nextCursor}`).then((next) =>
                setData((prev) => ({ ...next, items: [...prev.items, ...next.items] })),
              )
            }
          >
            Load more
          </Button>
        </div>
      )}

      <footer className="mt-10 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-faint">
        <span>
          <kbd className="rounded border border-edge px-1">⌘K</kbd> search
        </span>
        <span>
          Focus a card, then <kbd className="rounded border border-edge px-1">a</kbd> approve ·{' '}
          <kbd className="rounded border border-edge px-1">r</kbd> reject ·{' '}
          <kbd className="rounded border border-edge px-1">d</kbd> defer ·{' '}
          <kbd className="rounded border border-edge px-1">e</kbd> execute
        </span>
      </footer>

      <EditModal
        item={editing}
        open={editing !== null}
        saving={saving}
        onClose={() => setEditing(null)}
        onSave={saveEdit}
      />

      <ExecuteModal
        item={executeTarget}
        open={executeTarget !== null}
        preview={preview}
        loadingPreview={loadingPreview}
        executing={executing}
        error={executeError}
        onClose={() => setExecuteTarget(null)}
        onConfirm={confirmExecute}
      />

      <RunWorkflowModal
        item={workflowTarget}
        open={workflowTarget !== null}
        preview={workflowPreview}
        loadingPreview={loadingWorkflowPreview}
        running={runningWorkflow}
        result={workflowResult}
        onClose={() => setWorkflowTarget(null)}
        onConfirm={confirmWorkflow}
      />
    </>
  )
}

function toneFor(readiness: Readiness): string {
  return readiness === 'READY'
    ? 'text-risk-low'
    : readiness === 'NEEDS_CLARIFICATION'
      ? 'text-risk-medium'
      : 'text-ink-faint'
}
