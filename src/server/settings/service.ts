/**
 * `SettingsView` — SPEC-005 §4.1.
 *
 * The use cases that used to live here (get/update the guardrail envelope) are now served
 * by FastAPI (`apps/api/app/services/settings_profile.py`, SPEC-015 §7). This interface is
 * kept as a type-only export because the frontend's `import type` still describes the
 * FastAPI response with it, and duplicating the shape elsewhere would just be a second
 * place for it to drift.
 */

import type { ActionType, ApprovalGate } from '@/domain/types'

export interface SettingsView {
  timeZone: string
  workdayStart: string
  workdayEnd: string
  allowWeekends: boolean
  maxMeetingMinutes: number
  minBufferMinutes: number
  orgDomains: string[]
  orgCurrency: string
  budgetApprovalLimit: number
  autoExecuteLowRisk: boolean
  approvalThresholds: Partial<Record<ActionType, ApprovalGate>>
  providerRouting: Partial<Record<ActionType, string>>
  /** Capabilities with more than one adapter, so the UI knows what to offer. */
  routingOptions: {
    capability: string
    providers: { id: string; displayName: string; isDefault: boolean; note: string }[]
  }[]
  /** Time zones the runtime actually supports, for the picker. */
  timeZones: string[]
}
