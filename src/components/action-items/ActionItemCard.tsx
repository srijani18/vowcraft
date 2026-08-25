'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useState } from 'react'
import { Badge, Button } from '@/components/ui/primitives'
import type { ActionItemDTO } from '@/server/action-items/dto'

/**
 * One reviewable action. The card's job is to make a decision possible without
 * opening anything: what, who, when, how sure, how risky, and the sentence from
 * the meeting that produced it.
 */

const PRIORITY_STRIPE: Record<string, string> = {
  HIGH: 'bg-prio-high',
  MEDIUM: 'bg-prio-medium',
  LOW: 'bg-prio-low',
}

const TYPE_ICON: Record<string, string> = {
  CALENDAR: 'bi-calendar-event',
  TASK: 'bi-check2-square',
  EMAIL: 'bi-envelope',
  REMINDER: 'bi-bell',
  NONE: 'bi-info-circle',
}

const CONFIDENCE_TONE = { HIGH: 'success', MEDIUM: 'warn', LOW: 'danger' } as const
const RISK_TONE = { LOW: 'success', MEDIUM: 'warn', HIGH: 'danger' } as const
const STATUS_TONE: Record<string, 'neutral' | 'accent' | 'success' | 'warn' | 'danger' | 'muted'> = {
  PROPOSED: 'neutral',
  APPROVED: 'accent',
  DEFERRED: 'warn',
  REJECTED: 'muted',
  EXECUTING: 'accent',
  EXECUTED: 'success',
  FAILED: 'danger',
}

export function ActionItemCard({
  item,
  busy,
  focused,
  onFocus,
  onDecide,
  onEdit,
  onExecute,
  onRunWorkflow,
  onDelete,
}: {
  item: ActionItemDTO
  busy: boolean
  focused: boolean
  onFocus(): void
  onDecide(status: 'APPROVED' | 'REJECTED' | 'DEFERRED' | 'PROPOSED'): void
  onEdit(): void
  onExecute(): void
  onRunWorkflow(): void
  onDelete(): void
}) {
  const [quoteOpen, setQuoteOpen] = useState(false)
  const executed = item.status === 'EXECUTED'
  const result = item.executionResult as
    | { summary?: string; externalUrl?: string | null; simulated?: boolean; error?: { message: string } | null }
    | null

  return (
    <article
      tabIndex={0}
      onFocus={onFocus}
      aria-busy={busy}
      className={clsx(
        'group panel relative overflow-hidden rounded-2xl pl-1 outline-none transition-all duration-150',
        'hover:border-white/20 focus-visible:border-accent/60',
        focused && 'border-accent/40 shadow-glow',
        busy && 'opacity-60',
      )}
    >
      {/* Priority as a stripe rather than a badge: scannable down a long lane. */}
      <span
        className={clsx('absolute inset-y-0 left-0 w-1', PRIORITY_STRIPE[item.priority])}
        aria-hidden
      />

      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              <Badge tone="neutral" icon={TYPE_ICON[item.actionType]}>
                {item.actionType === 'NONE' ? 'Note' : title(item.actionType)}
              </Badge>
              <Badge tone={STATUS_TONE[item.status] ?? 'neutral'}>{title(item.status)}</Badge>
              <Badge
                tone={CONFIDENCE_TONE[item.confidence as keyof typeof CONFIDENCE_TONE]}
                icon="bi-graph-up"
                title="How sure the extractor was about this item"
              >
                {item.confidence}
              </Badge>
              {item.actionType !== 'NONE' && (
                <Badge
                  tone={RISK_TONE[item.riskTier as keyof typeof RISK_TONE]}
                  icon="bi-shield-exclamation"
                  title={item.riskFactors.join(' ')}
                >
                  {item.riskLabel}
                </Badge>
              )}
            </div>

            <h3 className="text-[15px] font-semibold leading-snug text-ink">{item.description}</h3>

            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
              <span className="inline-flex items-center gap-1.5">
                <i className="bi bi-person" aria-hidden />
                {item.ownerName ?? <span className="text-risk-medium">unassigned</span>}
              </span>
              {item.deadline && (
                <span className="inline-flex items-center gap-1.5">
                  <i className="bi bi-calendar3" aria-hidden />
                  {new Date(item.deadline).toLocaleString(undefined, {
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              )}
              {item.sourceTimestampLabel && (
                /*
                 * The grounding loop closed: the citation is a link that opens the
                 * transcript at that moment. Until this existed, a reviewer had to take
                 * both the timestamp and the quote on trust.
                 */
                item.transcript ? (
                  <Link
                    href={`/dashboard/transcripts/${item.transcript.id}?t=${item.sourceTimestampMs ?? 0}`}
                    className="inline-flex items-center gap-1.5 transition-colors hover:text-accent hover:underline"
                    title="Open the transcript at this moment and hear it"
                  >
                    <i className="bi bi-stopwatch" aria-hidden />
                    <span className="mono-num">{item.sourceTimestampLabel}</span>
                  </Link>
                ) : (
                  <span className="inline-flex items-center gap-1.5" title="Where this was said in the recording">
                    <i className="bi bi-stopwatch" aria-hidden />
                    <span className="mono-num">{item.sourceTimestampLabel}</span>
                  </span>
                )
              )}
              {item.transcript && (
                <Link
                  href={`/dashboard/transcripts/${item.transcript.id}`}
                  className="inline-flex items-center gap-1.5 truncate transition-colors hover:text-accent hover:underline"
                  title={`Read ${item.transcript.title}`}
                >
                  <i className="bi bi-soundwave" aria-hidden />
                  <span className="truncate">{item.transcript.title}</span>
                </Link>
              )}
            </div>
          </div>
        </div>

        {/* Missing detail is named, so "needs clarification" is never a dead end. */}
        {item.missingFields.length > 0 && (
          <p className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-ink-faint">needs</span>
            {item.missingFields.map((field) => (
              <span
                key={field}
                className="rounded-md border border-risk-medium/40 bg-risk-medium/10 px-1.5 py-0.5 font-mono text-[11px] text-risk-medium"
              >
                {field}
              </span>
            ))}
          </p>
        )}

        {item.violations.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {item.violations.slice(0, 3).map((v) => (
              <li
                key={v.ruleId}
                className={clsx(
                  'flex items-start gap-2 rounded-lg border px-2.5 py-1.5 text-xs',
                  v.severity === 'BLOCK'
                    ? 'border-risk-high/30 bg-risk-high/[0.07] text-risk-high'
                    : v.severity === 'WARN'
                      ? 'border-risk-medium/30 bg-risk-medium/[0.07] text-risk-medium'
                      : 'border-edge bg-surface text-ink-muted',
                )}
              >
                <i
                  className={clsx(
                    'bi mt-0.5',
                    v.severity === 'BLOCK' ? 'bi-slash-circle' : 'bi-exclamation-triangle',
                  )}
                  aria-hidden
                />
                <span className="min-w-0">
                  {v.message}
                  {v.remedy && <span className="text-ink-faint"> {v.remedy}</span>}
                  <span className="mono-num ml-1.5 opacity-50">{v.ruleId}</span>
                </span>
              </li>
            ))}
          </ul>
        )}

        {item.supersededBy && (
          <p className="mt-3 rounded-lg border border-edge bg-surface px-2.5 py-1.5 text-xs text-ink-muted">
            <i className="bi bi-arrow-right-circle mr-1.5" aria-hidden />
            Replaced later in the meeting by “{item.supersededBy.description}”.
          </p>
        )}

        {item.dependsOn && (
          <p className="mt-3 rounded-lg border border-edge bg-surface px-2.5 py-1.5 text-xs text-ink-muted">
            <i className="bi bi-link-45deg mr-1.5" aria-hidden />
            Blocked until “{item.dependsOn.description}” is done ({title(item.dependsOn.status)}).
          </p>
        )}

        {item.blocksCount > 0 && (
          <button
            onClick={onRunWorkflow}
            className="mt-3 flex w-full items-center gap-1.5 rounded-lg border border-accent-fill/30 bg-accent-fill/10 px-2.5 py-1.5 text-left text-xs text-accent transition-colors hover:bg-accent-fill/20"
          >
            <i className="bi bi-diagram-3-fill" aria-hidden />
            Blocks {item.blocksCount} other item{item.blocksCount === 1 ? '' : 's'} — run workflow
          </button>
        )}

        {executed && result?.summary && (
          <div className="mt-3 rounded-xl border border-risk-low/30 bg-risk-low/[0.07] px-3 py-2 text-xs">
            <p className="flex items-center gap-1.5 font-medium text-risk-low">
              <i className="bi bi-check-circle-fill" aria-hidden />
              {result.summary}
              {result.simulated && (
                <span className="rounded border border-edge px-1 text-[10px] text-ink-faint">simulated</span>
              )}
            </p>
            {result.externalUrl && (
              <a
                href={result.externalUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-accent hover:underline"
              >
                Open in {item.provider?.replace(/_/g, ' ')} <i className="bi bi-box-arrow-up-right text-[10px]" aria-hidden />
              </a>
            )}
          </div>
        )}

        {item.status === 'FAILED' && result?.error && (
          <div className="mt-3 rounded-xl border border-risk-high/30 bg-risk-high/[0.07] px-3 py-2 text-xs text-risk-high">
            <i className="bi bi-x-octagon-fill mr-1.5" aria-hidden />
            {result.error.message}
            <span className="text-ink-faint"> · {item.executionAttempts} attempt(s)</span>
          </div>
        )}

        {item.sourceQuote && (
          <div className="mt-3">
            <button
              onClick={() => setQuoteOpen((v) => !v)}
              aria-expanded={quoteOpen}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-faint hover:text-ink-muted"
            >
              <i className={clsx('bi', quoteOpen ? 'bi-chevron-down' : 'bi-chevron-right')} aria-hidden />
              {quoteOpen ? 'Hide' : 'Show'} what was said
            </button>
            {quoteOpen && (
              <blockquote className="mt-2 border-l-2 border-accent/40 bg-abyss/[0.05] dark:bg-abyss/30 px-3 py-2 text-xs italic leading-relaxed text-ink-muted">
                “{item.sourceQuote}”
                {item.reasoning && (
                  <footer className="mt-2 not-italic text-ink-faint">
                    <i className="bi bi-cpu mr-1" aria-hidden />
                    {item.reasoning}
                  </footer>
                )}
                {item.transcript && (
                  <Link
                    href={`/dashboard/transcripts/${item.transcript.id}?t=${item.sourceTimestampMs ?? 0}`}
                    className="mt-2 inline-flex items-center gap-1.5 not-italic text-xs font-medium text-accent hover:underline"
                  >
                    <i className="bi bi-play-circle" aria-hidden />
                    Hear this in the recording
                  </Link>
                )}
              </blockquote>
            )}
          </div>
        )}

        {item.gateNote && (
          <p className="mt-3 text-xs text-risk-medium">
            <i className="bi bi-exclamation-circle mr-1.5" aria-hidden />
            {item.gateNote}
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-edge pt-3">
          {item.status !== 'EXECUTED' && item.status !== 'EXECUTING' && (
            <>
              {item.status !== 'APPROVED' && (
                <Button variant="success" icon="bi-check-lg" onClick={() => onDecide('APPROVED')} disabled={busy}>
                  Approve
                </Button>
              )}
              <Button variant="secondary" icon="bi-pencil" onClick={onEdit} disabled={busy}>
                Edit
              </Button>
              {item.status !== 'REJECTED' && (
                <Button variant="danger" icon="bi-x-lg" onClick={() => onDecide('REJECTED')} disabled={busy}>
                  Reject
                </Button>
              )}
              {item.status !== 'DEFERRED' && item.status !== 'REJECTED' && (
                <Button variant="ghost" icon="bi-clock-history" onClick={() => onDecide('DEFERRED')} disabled={busy}>
                  Defer
                </Button>
              )}
              {item.status === 'REJECTED' && (
                <Button variant="ghost" icon="bi-arrow-counterclockwise" onClick={() => onDecide('PROPOSED')} disabled={busy}>
                  Reopen
                </Button>
              )}
            </>
          )}

          {/* Ghost, not danger: this removes a row from a board, and the loud red is
              reserved for Reject, which is a *decision* about the action rather than
              housekeeping. The audit trail survives either way — `AuditLog.actionItemId`
              is SET NULL, so what was proposed, approved and executed stays readable. */}
          <Button
            variant="ghost"
            icon="bi-trash"
            onClick={onDelete}
            disabled={busy}
            aria-label="Delete this action item"
          >
            Delete
          </Button>

          {/* Execute stays visible but disabled with the reason, so the gate is
              legible rather than mysterious. */}
          {item.actionType !== 'NONE' && item.status !== 'EXECUTED' && (
            <Button
              variant="primary"
              icon={item.status === 'FAILED' ? 'bi-arrow-clockwise' : 'bi-lightning-charge-fill'}
              onClick={onExecute}
              disabled={busy || !item.canExecute}
              title={item.blockedReason ?? undefined}
              className="ml-auto"
            >
              {item.status === 'FAILED' ? 'Retry' : 'Execute'}
            </Button>
          )}

          {!item.canExecute && item.blockedReason && item.status !== 'EXECUTED' && (
            <span className="w-full text-[11px] text-ink-faint">{item.blockedReason}</span>
          )}
        </div>
      </div>
    </article>
  )
}

function title(value: string): string {
  return value.charAt(0) + value.slice(1).toLowerCase().replace(/_/g, ' ')
}
