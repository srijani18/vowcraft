'use client'

import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { Modal } from '@/components/ui/Modal'
import { Badge, Button, Skeleton, inputClass } from '@/components/ui/primitives'
import type { ActionItemDTO } from '@/server/action-items/dto'
import type { ExecuteOutcome } from './api'

/**
 * Confirmation before anything reaches a third party (SPEC-001 §11).
 *
 * The preview comes from a server dry run, which runs the *same* normalisation,
 * validation, and guardrail code as the real execution — so what is shown here
 * is what gets sent, not a client-side approximation of it.
 */

const RISK_STYLE = {
  LOW: { tone: 'success' as const, ring: 'border-risk-low/40' },
  MEDIUM: { tone: 'warn' as const, ring: 'border-risk-medium/40' },
  HIGH: { tone: 'danger' as const, ring: 'border-risk-high/40' },
}

export function ExecuteModal({
  item,
  open,
  preview,
  loadingPreview,
  executing,
  error,
  onClose,
  onConfirm,
}: {
  item: ActionItemDTO | null
  open: boolean
  preview: ExecuteOutcome | null
  loadingPreview: boolean
  executing: boolean
  error: string | null
  onClose(): void
  onConfirm(acknowledgedWarnings: string[]): void
}) {
  const [acked, setAcked] = useState(false)
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (open) {
      setAcked(false)
      setTyped('')
    }
  }, [open, item?.id])

  if (!item) return null

  const risk = RISK_STYLE[(preview?.riskTier ?? item.riskTier) as keyof typeof RISK_STYLE]
  const warnings = preview?.warnings ?? []
  const needsTyped = (preview?.approvalGate ?? item.approvalGate) === 'EXPLICIT_APPROVAL_WITH_CONFIRMATION'
  const blocked = preview !== null && !preview.ok

  // A HIGH-risk action requires the word typed out: the point is to break the
  // rhythm of clicking through dialogs, not to add a second click.
  const typedOk = !needsTyped || typed.trim().toUpperCase() === 'EXECUTE'
  const ackOk = warnings.length === 0 || acked
  const canConfirm = !loadingPreview && !blocked && typedOk && ackOk && !executing

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Confirm execution"
      subtitle={preview?.preview?.consequence ?? 'Checking what this will do…'}
      icon="bi-lightning-charge-fill"
      width="max-w-xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={preview?.riskTier === 'HIGH' ? 'danger' : 'primary'}
            icon="bi-send-fill"
            onClick={() => onConfirm(warnings.map((w) => w.ruleId))}
            loading={executing}
            disabled={!canConfirm}
          >
            {executing ? 'Executing…' : 'Execute now'}
          </Button>
        </>
      }
    >
      {loadingPreview ? (
        <div className="space-y-3">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : (
        <div className="space-y-4">
          <div className={clsx('flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2', risk.ring)}>
            <Badge tone={risk.tone} icon="bi-shield-exclamation">
              {preview?.riskTier ?? item.riskTier} risk
            </Badge>
            <Badge tone="neutral" icon="bi-plug">
              {preview?.preview?.provider?.replace(/_/g, ' ') ?? item.providerId?.replace(/_/g, ' ')}
            </Badge>
            <Badge tone={item.confidence === 'HIGH' ? 'success' : 'warn'} icon="bi-graph-up">
              {item.confidence} confidence
            </Badge>
          </div>

          {(preview?.riskFactors.length ?? 0) > 0 && (
            <ul className="space-y-1 text-xs text-ink-muted">
              {preview!.riskFactors.map((factor) => (
                <li key={factor} className="flex gap-2">
                  <i className="bi bi-dot mt-0.5 text-ink-faint" aria-hidden />
                  {factor}
                </li>
              ))}
            </ul>
          )}

          {/* The exact payload, field by field — nothing hidden behind a summary. */}
          {preview?.preview && (
            <dl className="divide-y divide-white/5 overflow-hidden rounded-xl border border-edge bg-abyss/[0.06] dark:bg-abyss/40">
              {preview.preview.fields.map((field) => (
                <div key={field.label} className="grid grid-cols-[110px_1fr] gap-3 px-3 py-2">
                  <dt className="text-xs font-medium text-ink-faint">{field.label}</dt>
                  <dd className="min-w-0 whitespace-pre-wrap break-words text-xs text-ink">{field.value}</dd>
                </div>
              ))}
            </dl>
          )}

          {blocked && (
            <div className="rounded-xl border border-risk-high/40 bg-risk-high/[0.08] px-3 py-2.5 text-xs text-risk-high">
              <p className="font-medium">
                <i className="bi bi-slash-circle mr-1.5" aria-hidden />
                A guardrail blocks this action.
              </p>
              <ul className="mt-1.5 space-y-1">
                {item.violations
                  .filter((v) => v.severity === 'BLOCK')
                  .map((v) => (
                    <li key={v.ruleId}>
                      {v.message} <span className="mono-num opacity-60">{v.ruleId}</span>
                    </li>
                  ))}
              </ul>
            </div>
          )}

          {warnings.length > 0 && (
            <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-risk-medium/40 bg-risk-medium/[0.07] px-3 py-2.5">
              <input
                type="checkbox"
                checked={acked}
                onChange={(e) => setAcked(e.target.checked)}
                className="mt-0.5 size-4 accent-warn"
              />
              <span className="text-xs text-risk-medium">
                <span className="font-medium">I understand:</span>
                <ul className="mt-1 space-y-0.5">
                  {warnings.map((w) => (
                    <li key={w.ruleId}>{w.message}</li>
                  ))}
                </ul>
              </span>
            </label>
          )}

          {needsTyped && !blocked && (
            <div className="rounded-xl border border-risk-high/40 bg-risk-high/[0.07] px-3 py-2.5">
              <p className="text-xs font-medium text-risk-high">
                <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
                High risk. Type EXECUTE to confirm.
              </p>
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder="EXECUTE"
                aria-label="Type EXECUTE to confirm"
                className={clsx(inputClass, 'mt-2 font-mono uppercase')}
              />
            </div>
          )}

          {preview?.replayed && (
            <p className="text-xs text-ink-muted">
              <i className="bi bi-arrow-repeat mr-1.5" aria-hidden />
              This has already been executed with these details; running it again returns the original result.
            </p>
          )}

          {error && (
            <p className="rounded-xl border border-risk-high/40 bg-risk-high/[0.08] px-3 py-2 text-xs text-risk-high">
              {error}
            </p>
          )}

          {preview?.preview && (
            <p className="text-[11px] text-ink-faint">
              <i className="bi bi-info-circle mr-1" aria-hidden />
              Every execution is recorded in the audit log with the exact payload sent.
            </p>
          )}
        </div>
      )}
    </Modal>
  )
}
