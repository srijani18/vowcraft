'use client'

import { apiJson, ApiError } from '@/lib/api-client'
import type { ActionItemDTO } from '@/server/action-items/dto'
import type { ListResult } from '@/server/action-items/service'

/** Thin client for the action-item API — SPEC-015 §7. Talks to the FastAPI backend
 * directly (a different origin from the Next.js app), carrying the bearer token. */

export { ApiError }

/** The field-by-field confirmation table shown before anything is sent — a port of
 * `apps/api/app/integrations/adapters.py`'s per-provider `preview()` output. */
export interface ExecutionPreview {
  provider: string
  /** "Creates a calendar event for 3 people" — the consequence, in one line. */
  consequence: string
  fields: { label: string; value: string }[]
}

export interface ExecutionResultJson {
  provider: string
  mode: 'mock' | 'live'
  externalId: string
  externalUrl?: string | null
  /** One sentence for the success toast — what `outcome.result?.summary` renders. */
  summary: string
  simulated: boolean
  replayed?: boolean
  error?: { code: string; message: string } | null
}

/** The response from `POST /api/action-items/{id}/execute` — SPEC-002 §5. */
export interface ExecuteOutcome {
  ok: boolean
  dryRun: boolean
  replayed: boolean
  status: string
  riskTier: string
  riskFactors: string[]
  approvalGate: string
  warnings: { ruleId: string; message: string }[]
  preview?: ExecutionPreview
  result: ExecutionResultJson | null
}

export function fetchList(search: string): Promise<ListResult> {
  return apiJson<ListResult>(`/api/action-items${search ? `?${search}` : ''}`)
}

export function patchItem(id: string, body: Record<string, unknown>): Promise<ActionItemDTO> {
  return apiJson<ActionItemDTO>(`/api/action-items/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteItem(id: string): Promise<{ ok: boolean }> {
  return apiJson<{ ok: boolean }>(`/api/action-items/${id}`, { method: 'DELETE' })
}

export function bulkOp(ids: string[], op: 'approve' | 'reject' | 'defer') {
  return apiJson<{ okCount: number; failedCount: number; results: { id: string; ok: boolean; error?: string }[] }>(
    '/api/action-items/bulk',
    { method: 'POST', body: JSON.stringify({ ids, op }) },
  )
}

export function executeItem(
  id: string,
  body: {
    dryRun?: boolean
    payloadOverride?: Record<string, unknown>
    acknowledgedWarnings?: string[]
    confirmed?: boolean
  },
): Promise<ExecuteOutcome> {
  return apiJson<ExecuteOutcome>(`/api/action-items/${id}/execute`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** The chain `runWorkflow` would attempt, and in what order — SPEC-002 §8. */
export interface WorkflowPreview {
  items: { id: string; description: string; status: string; actionType: string }[]
}

/** The response from `POST /api/action-items/{id}/run-workflow` — SPEC-002 §8. */
export interface WorkflowOutcome {
  startedId: string
  ok: boolean
  steps: { id: string; ok: boolean; replayed: boolean; status: string; result: ExecutionResultJson | null }[]
  haltedAt: { id: string; statusCode: number; code: string; message: string } | null
  skippedIds: string[]
}

export function getWorkflow(id: string): Promise<WorkflowPreview> {
  return apiJson<WorkflowPreview>(`/api/action-items/${id}/workflow`)
}

export function runWorkflow(id: string, body: { confirmed?: boolean } = {}): Promise<WorkflowOutcome> {
  return apiJson<WorkflowOutcome>(`/api/action-items/${id}/run-workflow`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
