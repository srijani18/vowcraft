import { db } from '@/lib/db'
import { env } from '@/lib/env'
import { moduleAvailability } from '@/lib/credentials/service'
import { providerStatuses } from '@/integrations/registry'
import type { ModuleAvailability } from '@/lib/credentials/types'
import { evaluate, toCore } from '../action-items/dto'
import { loadEvaluationInputs } from '../action-items/service'

/**
 * Landing overview and audit log queries — SPEC-005 §7, SPEC-003 §7.
 *
 * Everything is aggregated from `ActionItem`, `ExecutionAttempt`, and `AuditLog`
 * at read time. No counter tables and no analytics store: a denormalised count
 * that drifts from the rows it describes is worse than a slightly slower query,
 * because someone will make a decision from it.
 */

// ───────────────────────────────────────────────────────────── overview ──

export interface FunnelStage {
  key: string
  label: string
  count: number
  /** Share of the stage above it, so the drop-off is visible. */
  ofPrevious: number | null
  hint: string
}

export interface RuleActivity {
  ruleId: string
  severity: string
  count: number
}

export interface OverviewData {
  generatedAt: string
  headline: {
    pendingDecisions: number
    readyToExecute: number
    executedAllTime: number
    failedNeedingAttention: number
  }
  byReadiness: { READY: number; NEEDS_CLARIFICATION: number; INFORMATIONAL: number }
  byStatus: Record<string, number>
  byActionType: Record<string, number>
  byPriority: Record<string, number>
  funnel: FunnelStage[]
  rates: {
    decisionRate: number
    approvalRate: number
    executionSuccessRate: number
    avgExecutionMs: number | null
  }
  guardrails: {
    blockedExecutions: number
    topRules: RuleActivity[]
  }
  transcripts: {
    total: number
    totalDurationMs: number
    languages: string[]
    recent: { id: string; title: string; recordedAt: string | null; actionItems: number }[]
  }
  usage: {
    executionAttempts: number
    successfulExecutions: number
    simulatedExecutions: number
    correctionsCaptured: number
    auditEvents: number
    integrationsConnected: number
    providersRegistered: number
  }
  system: {
    integrationsMode: string
    modules: ModuleAvailability[]
    providers: ReturnType<typeof providerStatuses>
  }
  activity: { at: string; event: string; description: string | null }[]
}

const pct = (part: number, whole: number) => (whole === 0 ? 0 : Math.round((part / whole) * 1000) / 10)

export async function getOverview(userId: string, userEmail: string): Promise<OverviewData> {
  const scope = { transcript: { userId } } as const

  const [
    statusGroups,
    typeGroups,
    priorityGroups,
    items,
    transcriptAgg,
    transcripts,
    attemptGroups,
    attemptStats,
    corrections,
    auditCount,
    blockedCount,
    ruleRows,
    accounts,
    modules,
    recentAudit,
  ] = await Promise.all([
    db.actionItem.groupBy({ by: ['status'], where: scope, _count: true }),
    db.actionItem.groupBy({ by: ['actionType'], where: scope, _count: true }),
    db.actionItem.groupBy({ by: ['priority'], where: scope, _count: true }),
    // Readiness is derived from the payload, so it cannot be grouped in SQL —
    // the full rows are needed to run the real evaluation over them.
    db.actionItem.findMany({ where: scope }),
    db.transcript.aggregate({ where: { userId }, _sum: { durationMs: true }, _count: true }),
    db.transcript.findMany({
      where: { userId },
      select: {
        id: true,
        title: true,
        recordedAt: true,
        language: true,
        _count: { select: { actionItems: true } },
      },
      orderBy: { createdAt: 'desc' },
      take: 6,
    }),
    db.executionAttempt.groupBy({
      by: ['outcome'],
      where: { actionItem: scope },
      _count: true,
    }),
    db.executionAttempt.aggregate({
      where: { actionItem: scope, outcome: 'SUCCESS' },
      _avg: { durationMs: true },
    }),
    db.correction.count({ where: { actionItem: scope } }),
    db.auditLog.count({ where: { actorId: userId } }),
    db.auditLog.count({ where: { actorId: userId, event: 'action_item.guardrail_blocked' } }),
    db.auditLog.findMany({
      where: { actorId: userId, event: { in: ['action_item.guardrail_blocked', 'action_item.executed'] } },
      select: { metadata: true },
      take: 500,
      orderBy: { at: 'desc' },
    }),
    db.integrationAccount.count({ where: { userId, needsReauth: false } }),
    moduleAvailability(userId),
    db.auditLog.findMany({
      where: { actorId: userId },
      select: { at: true, event: true, actionItem: { select: { description: true } } },
      orderBy: { at: 'desc' },
      take: 12,
    }),
  ])

  const byStatus = Object.fromEntries(statusGroups.map((g) => [g.status, g._count])) as Record<string, number>
  const byActionType = Object.fromEntries(typeGroups.map((g) => [g.actionType, g._count])) as Record<string, number>
  const byPriority = Object.fromEntries(priorityGroups.map((g) => [g.priority, g._count])) as Record<string, number>

  // Readiness is derived, so it must be computed — but through the *same* code
  // path the board uses, not a second implementation of the same idea. An earlier
  // version reimplemented the missing-field and confidence checks here and
  // silently disagreed with the board by two items, because it skipped the rule
  // engine: an approved-but-guardrail-blocked action counted as READY.
  // SPEC-005 §9.8 requires the two surfaces to agree, and sharing `evaluate()` is
  // the only way to keep that true as rules are added.
  const inputs = await loadEvaluationInputs(userId, userEmail)
  const byReadiness = { READY: 0, NEEDS_CLARIFICATION: 0, INFORMATIONAL: 0 }
  const now = new Date()
  for (const item of items) {
    const { readiness } = evaluate(toCore(item), inputs, item.transcriptId, now)
    byReadiness[readiness.readiness] += 1
  }

  const total = items.length
  const decided = total - (byStatus.PROPOSED ?? 0)
  const approved = (byStatus.APPROVED ?? 0) + (byStatus.EXECUTING ?? 0) + (byStatus.EXECUTED ?? 0) + (byStatus.FAILED ?? 0)
  const executed = byStatus.EXECUTED ?? 0

  const funnel: FunnelStage[] = [
    { key: 'extracted', label: 'Extracted', count: total, ofPrevious: null, hint: 'Action items the extractor produced.' },
    { key: 'decided', label: 'Decided', count: decided, ofPrevious: pct(decided, total), hint: 'A human approved, rejected, or deferred them.' },
    { key: 'approved', label: 'Approved', count: approved, ofPrevious: pct(approved, decided), hint: 'Cleared to execute.' },
    { key: 'executed', label: 'Executed', count: executed, ofPrevious: pct(executed, approved), hint: 'A real side effect exists.' },
  ]

  const attempts = Object.fromEntries(attemptGroups.map((g) => [g.outcome, g._count])) as Record<string, number>
  const successful = attempts.SUCCESS ?? 0
  const failedAttempts = attempts.FAILED ?? 0

  // Rule activity from audit metadata: `rules: ["SCHED_BUFFER:WARN", …]`.
  const ruleCounts = new Map<string, number>()
  for (const row of ruleRows) {
    const meta = row.metadata as { rules?: unknown } | null
    const rules = Array.isArray(meta?.rules) ? meta!.rules : []
    for (const entry of rules) {
      if (typeof entry !== 'string') continue
      ruleCounts.set(entry, (ruleCounts.get(entry) ?? 0) + 1)
    }
  }
  const topRules: RuleActivity[] = [...ruleCounts.entries()]
    .map(([entry, count]) => {
      const [ruleId, severity = 'INFO'] = entry.split(':')
      return { ruleId: ruleId!, severity, count }
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 8)

  const simulated = await db.executionAttempt.count({
    where: { actionItem: scope, outcome: 'SUCCESS', mode: 'mock' },
  })

  return {
    generatedAt: new Date().toISOString(),
    headline: {
      pendingDecisions: byStatus.PROPOSED ?? 0,
      readyToExecute: byStatus.APPROVED ?? 0,
      executedAllTime: executed,
      failedNeedingAttention: byStatus.FAILED ?? 0,
    },
    byReadiness,
    byStatus,
    byActionType,
    byPriority,
    funnel,
    rates: {
      decisionRate: pct(decided, total),
      approvalRate: pct(approved, decided),
      executionSuccessRate: pct(successful, successful + failedAttempts),
      avgExecutionMs: attemptStats._avg.durationMs === null ? null : Math.round(attemptStats._avg.durationMs),
    },
    guardrails: { blockedExecutions: blockedCount, topRules },
    transcripts: {
      total: transcriptAgg._count,
      totalDurationMs: transcriptAgg._sum.durationMs ?? 0,
      languages: [...new Set(transcripts.map((t) => t.language).filter((l): l is string => Boolean(l)))],
      recent: transcripts.map((t) => ({
        id: t.id,
        title: t.title,
        recordedAt: t.recordedAt?.toISOString() ?? null,
        actionItems: t._count.actionItems,
      })),
    },
    usage: {
      executionAttempts: successful + failedAttempts + (attempts.RUNNING ?? 0),
      successfulExecutions: successful,
      simulatedExecutions: simulated,
      correctionsCaptured: corrections,
      auditEvents: auditCount,
      integrationsConnected: accounts,
      providersRegistered: providerStatuses().length,
    },
    system: {
      integrationsMode: env().INTEGRATIONS_MODE,
      modules,
      providers: providerStatuses(),
    },
    activity: recentAudit.map((a) => ({
      at: a.at.toISOString(),
      event: a.event,
      description: a.actionItem?.description ?? null,
    })),
  }
}

// ──────────────────────────────────────────────────────────── audit log ──
//
// The query/list use cases that used to live here are now served by FastAPI
// (`apps/api/app/services/audit.py`'s `AuditService`, SPEC-015 §7). These two
// interfaces are kept as type-only exports because the frontend's `import type` still
// describes the FastAPI response with them, and duplicating the shape elsewhere would
// just be a second place for it to drift.

export interface AuditEntryView {
  id: string
  at: string
  event: string
  actorType: string
  actorLabel: string
  actionItemId: string | null
  actionItemDescription: string | null
  before: unknown
  after: unknown
  metadata: unknown
  requestId: string | null
}

export interface AuditResult {
  entries: AuditEntryView[]
  facets: { events: { event: string; count: number }[] }
  nextCursor: string | null
  total: number
}
