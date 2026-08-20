import type { ActionItem, Prisma } from '@prisma/client'
import { computeReadiness } from '@/domain/action-item'
import { classifyRisk, effectiveGate } from '@/domain/risk'
import { evaluateRules } from '@/domain/rules'
import type {
  ActionItemCore,
  ApprovalGate,
  Readiness,
  RiskTier,
  RuleContext,
  RuleViolation,
  SettingsView,
} from '@/domain/types'
import { resolveProvider } from '@/integrations/registry'

/**
 * The pure evaluation core (SPEC-001 §10, SPEC-003 §6) — readiness, risk, approval gate,
 * routed provider, all as a function of an `ActionItemCore` and the account's settings.
 *
 * This file used to also own the Prisma row → API DTO mapping (`toDTO`) for the Next.js
 * action-items API; that surface is now served by FastAPI (`apps/api/app/services/
 * action_items.py::to_dto`, SPEC-015 §7) and the mapping function moved there with it.
 * What remains here is still load-bearing: `evaluate`/`toCore` are the same functions the
 * landing-page analytics (`src/server/analytics/service.ts`) uses to compute readiness
 * for its counts, and `ActionItemDTO` is kept as the shared response-shape type the
 * frontend's `import type` still relies on even though nothing in this file produces one
 * anymore.
 */

export interface ActionItemDTO {
  id: string
  description: string
  actionType: string
  status: string
  priority: string
  confidence: string
  ownerName: string | null
  ownerEmail: string | null
  deadline: string | null
  sourceTimestampMs: number | null
  /** Pre-formatted "12:34" so every surface renders it identically. */
  sourceTimestampLabel: string | null
  sourceQuote: string | null
  reasoning: string | null
  payload: Record<string, unknown>
  executionResult: Prisma.JsonValue | null
  executionAttempts: number
  executedAt: string | null
  provider: string | null
  createdAt: string
  updatedAt: string

  // ── computed, authoritative, server-side only
  readiness: Readiness
  missingFields: string[]
  violations: RuleViolation[]
  riskTier: RiskTier
  riskFactors: string[]
  riskLabel: string
  approvalGate: ApprovalGate
  gateNote: string | null
  /** Null when no provider can serve this action type. */
  providerId: string | null
  canExecute: boolean
  blockedReason: string | null

  transcript: { id: string; title: string; recordedAt: string | null } | null
  supersededBy: { id: string; description: string } | null
  dependsOn: { id: string; description: string; status: string } | null
}

export interface EvaluationInputs {
  settings: SettingsView
  selfEmail: string
  busyBlocks: RuleContext['busyBlocks']
  teamMembers: RuleContext['teamMembers']
  decisionsByTranscript: Map<string, RuleContext['decisions']>
  providerRouting: Partial<Record<string, string>>
}

export function toCore(row: ActionItem): ActionItemCore {
  return {
    id: row.id,
    description: row.description,
    actionType: row.actionType,
    status: row.status,
    priority: row.priority,
    confidence: row.confidence,
    ownerName: row.ownerName,
    ownerEmail: row.ownerEmail,
    deadline: row.deadline,
    sourceTimestampMs: row.sourceTimestampMs,
    sourceQuote: row.sourceQuote,
    payload: (row.payload as Record<string, unknown> | null) ?? {},
    supersededById: row.supersededById,
    dependsOnId: row.dependsOnId,
  }
}

/**
 * Builds the full evaluation for one row. Called for every listed item *and*
 * again at execute time (SPEC-003 §6) — the same function, so what the reviewer
 * saw and what the executor enforces cannot drift apart.
 */
export function evaluate(
  core: ActionItemCore,
  inputs: EvaluationInputs,
  transcriptId: string | null,
  now: Date,
) {
  const ctx: RuleContext = {
    item: core,
    payload: core.payload,
    now,
    settings: inputs.settings,
    busyBlocks: inputs.busyBlocks,
    teamMembers: inputs.teamMembers,
    decisions: (transcriptId ? inputs.decisionsByTranscript.get(transcriptId) : undefined) ?? [],
    hasExplicitApproval: core.status === 'APPROVED' || core.status === 'FAILED',
  }

  const rules = evaluateRules(ctx)
  const readiness = computeReadiness(core, rules.violations)
  const risk = classifyRisk(core, core.payload, inputs.settings, inputs.selfEmail)
  const gate = effectiveGate(risk.tier, core.confidence, inputs.settings, core.actionType)
  const provider = resolveProvider(core.actionType, inputs.providerRouting)

  return { ctx, rules, readiness, risk, gate, provider }
}
