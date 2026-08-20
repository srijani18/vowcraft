import { DEFAULT_SETTINGS, type Readiness, type SettingsView } from '@/domain/types'
import { db } from '@/lib/db'
import type { ActionItemDTO, EvaluationInputs } from './dto'

/**
 * `loadEvaluationInputs` — the only runtime piece of this file still called from a live
 * request path: `src/server/analytics/service.ts`'s landing-page counts.
 *
 * The action-item use cases that used to live here (list/get/patch/bulk) are now served
 * by FastAPI (`apps/api/app/services/action_items.py`, SPEC-015 §7); `ListResult` is kept
 * as a type-only export because the frontend's `import type` still describes the FastAPI
 * response with it, and duplicating the shape elsewhere would just be a second place for
 * it to drift.
 */

/**
 * Loads everything the pure layer needs, once per request rather than per item.
 * The rule engine performs no I/O (SPEC-003 §2), so this is where its whole
 * world gets assembled.
 */
export async function loadEvaluationInputs(userId: string, userEmail: string): Promise<EvaluationInputs> {
  const [settingsRow, busyBlocks, teamMembers, decisions] = await Promise.all([
    db.userSettings.findUnique({ where: { userId } }),
    db.calendarBusyBlock.findMany({
      where: { userId, endsAt: { gte: new Date(Date.now() - 24 * 3_600_000) } },
      orderBy: { startsAt: 'asc' },
      take: 500,
    }),
    db.teamMember.findMany({ where: { userId }, select: { name: true, email: true } }),
    db.decision.findMany({
      where: { transcript: { userId } },
      select: { transcriptId: true, statement: true, sourceTimestampMs: true },
      take: 500,
    }),
  ])

  const settings: SettingsView = settingsRow
    ? {
        timeZone: settingsRow.timeZone,
        workdayStart: settingsRow.workdayStart,
        workdayEnd: settingsRow.workdayEnd,
        allowWeekends: settingsRow.allowWeekends,
        maxMeetingMinutes: settingsRow.maxMeetingMinutes,
        minBufferMinutes: settingsRow.minBufferMinutes,
        orgDomains: settingsRow.orgDomains,
        autoExecuteLowRisk: settingsRow.autoExecuteLowRisk,
        budgetApprovalLimit: settingsRow.budgetApprovalLimit,
        orgCurrency: settingsRow.orgCurrency,
        approvalThresholds: (settingsRow.approvalThresholds as SettingsView['approvalThresholds']) ?? {},
      }
    : DEFAULT_SETTINGS

  const decisionsByTranscript = new Map<string, { statement: string; sourceTimestampMs: number | null }[]>()
  for (const d of decisions) {
    const list = decisionsByTranscript.get(d.transcriptId) ?? []
    list.push({ statement: d.statement, sourceTimestampMs: d.sourceTimestampMs })
    decisionsByTranscript.set(d.transcriptId, list)
  }

  return {
    settings,
    selfEmail: userEmail,
    busyBlocks: busyBlocks.map((b) => ({
      title: b.title,
      startsAt: b.startsAt,
      endsAt: b.endsAt,
      kind: b.kind,
    })),
    teamMembers,
    decisionsByTranscript,
    providerRouting: (settingsRow?.providerRouting as Record<string, string>) ?? {},
  }
}

export interface ListResult {
  items: ActionItemDTO[]
  counts: Record<Readiness, number> & { total: number }
  facets: { owners: string[]; transcripts: { id: string; title: string }[] }
  nextCursor: string | null
}
