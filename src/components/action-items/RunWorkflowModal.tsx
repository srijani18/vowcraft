'use client'

import clsx from 'clsx'
import { Modal } from '@/components/ui/Modal'
import { Button, Skeleton } from '@/components/ui/primitives'
import type { ActionItemDTO } from '@/server/action-items/dto'
import type { WorkflowOutcome, WorkflowPreview } from './api'

/**
 * Confirmation before a chain of items runs — SPEC-002 §8.
 *
 * Deliberately simpler than `ExecuteModal`: each step still re-validates its own risk,
 * guardrails, and approval gate the moment it actually runs (this reuses `execute()`
 * entirely), so there is nothing per-step to preview here beyond order and identity — the
 * thing this modal actually needs to communicate is that a *halt* stops the whole run, not
 * just the item that failed.
 */

const TYPE_ICON: Record<string, string> = {
  CALENDAR: 'bi-calendar-event',
  TASK: 'bi-check2-square',
  EMAIL: 'bi-envelope',
  REMINDER: 'bi-bell',
  NONE: 'bi-info-circle',
}

export function RunWorkflowModal({
  item,
  open,
  preview,
  loadingPreview,
  running,
  result,
  onClose,
  onConfirm,
}: {
  item: ActionItemDTO | null
  open: boolean
  preview: WorkflowPreview | null
  loadingPreview: boolean
  running: boolean
  result: WorkflowOutcome | null
  onClose(): void
  onConfirm(): void
}) {
  if (!item) return null

  const stepStatus = (id: string): 'done' | 'halted' | 'skipped' | 'pending' => {
    if (!result) return 'pending'
    if (result.steps.some((s) => s.id === id)) return 'done'
    if (result.haltedAt?.id === id) return 'halted'
    if (result.skippedIds.includes(id)) return 'skipped'
    return 'pending'
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Run workflow"
      subtitle={
        result
          ? result.ok
            ? `All ${result.steps.length} steps executed.`
            : `Stopped after ${result.steps.length} of ${preview?.items.length ?? '?'} steps.`
          : `Runs this item and everything that depends on it, in order.`
      }
      icon="bi-diagram-3-fill"
      width="max-w-lg"
      footer={
        result ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon="bi-play-fill"
              onClick={onConfirm}
              loading={running}
              disabled={loadingPreview || running || !preview || preview.items.length === 0}
            >
              {running ? 'Running…' : `Run ${preview?.items.length ?? ''} steps`}
            </Button>
          </>
        )
      }
    >
      {loadingPreview ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : (
        <ol className="space-y-1.5">
          {preview?.items.map((step, i) => {
            const state = stepStatus(step.id)
            return (
              <li
                key={step.id}
                className={clsx(
                  'flex items-center gap-2.5 rounded-lg border px-2.5 py-2 text-xs',
                  state === 'halted' ? 'border-risk-high/40 bg-risk-high/[0.07]' : 'border-edge/25',
                )}
              >
                <span className="mono-num w-4 shrink-0 text-ink-faint">{i + 1}</span>
                <i className={clsx('bi', TYPE_ICON[step.actionType] ?? 'bi-info-circle', 'shrink-0 text-ink-faint')} aria-hidden />
                <span className="min-w-0 flex-1 truncate">{step.description}</span>
                {state === 'done' && <i className="bi bi-check-circle-fill shrink-0 text-ok" aria-hidden />}
                {state === 'halted' && <i className="bi bi-x-circle-fill shrink-0 text-danger" aria-hidden />}
                {state === 'skipped' && <span className="shrink-0 text-[10px] text-ink-faint">not attempted</span>}
              </li>
            )
          })}
        </ol>
      )}

      {result?.haltedAt && (
        <p className="mt-3 rounded-xl border border-risk-high/40 bg-risk-high/[0.08] px-3 py-2.5 text-xs text-risk-high">
          <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
          {result.haltedAt.message}
        </p>
      )}
    </Modal>
  )
}
