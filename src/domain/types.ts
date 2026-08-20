/**
 * Domain vocabulary. Deliberately declared as string-literal unions rather than
 * imported from `@prisma/client`, so `domain/` compiles and unit-tests without a
 * generated client, a database, or a network (SPEC-000 §3). Prisma emits string
 * enums, so these are structurally assignable in both directions.
 */

export type ActionType = 'CALENDAR' | 'TASK' | 'EMAIL' | 'REMINDER' | 'NONE'
export type Priority = 'HIGH' | 'MEDIUM' | 'LOW'
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW'
export type ActionStatus =
  | 'PROPOSED'
  | 'APPROVED'
  | 'REJECTED'
  | 'DEFERRED'
  | 'EXECUTING'
  | 'EXECUTED'
  | 'FAILED'

export type RiskTier = 'LOW' | 'MEDIUM' | 'HIGH'
export type ApprovalGate = 'AUTO' | 'EXPLICIT_APPROVAL' | 'EXPLICIT_APPROVAL_WITH_CONFIRMATION'

/** Derived, never stored — SPEC-001 §6. */
export type Readiness = 'READY' | 'NEEDS_CLARIFICATION' | 'INFORMATIONAL'

export type Severity = 'BLOCK' | 'WARN' | 'INFO'

/** The subset of an ActionItem the pure layer needs. Nothing Prisma-specific. */
export interface ActionItemCore {
  id: string
  description: string
  actionType: ActionType
  status: ActionStatus
  priority: Priority
  confidence: Confidence
  ownerName: string | null
  ownerEmail: string | null
  deadline: Date | null
  sourceTimestampMs: number | null
  sourceQuote: string | null
  payload: Record<string, unknown>
  supersededById: string | null
  dependsOnId: string | null
}

export interface SettingsView {
  timeZone: string
  workdayStart: string
  workdayEnd: string
  allowWeekends: boolean
  maxMeetingMinutes: number
  minBufferMinutes: number
  orgDomains: string[]
  autoExecuteLowRisk: boolean
  budgetApprovalLimit: number
  orgCurrency: string
  approvalThresholds: Partial<Record<ActionType, ApprovalGate>>
}

export interface TeamMemberView {
  name: string
  email: string
}

export interface BusyBlockView {
  title: string | null
  startsAt: Date
  endsAt: Date
  kind: 'BUSY' | 'FOCUS' | 'LUNCH' | 'OOO'
}

export interface DecisionView {
  statement: string
  sourceTimestampMs: number | null
}

/** Everything a rule may read. All of it is passed in; rules perform no I/O. */
export interface RuleContext {
  item: ActionItemCore
  payload: Record<string, unknown>
  now: Date
  settings: SettingsView
  busyBlocks: BusyBlockView[]
  teamMembers: TeamMemberView[]
  decisions: DecisionView[]
  /** True once a human has explicitly approved; policy rules consult it. */
  hasExplicitApproval: boolean
}

export interface RuleViolation {
  ruleId: string
  severity: Severity
  message: string
  /** Dotted path into the payload, when the problem is a specific field. */
  field?: string
  /** What would make this pass — shown verbatim in the UI. */
  remedy?: string
}

export interface Rule {
  id: string
  appliesTo: ActionType[]
  severity: Severity
  evaluate(ctx: RuleContext): RuleViolation | null
}

export const DEFAULT_SETTINGS: SettingsView = {
  timeZone: 'Asia/Kolkata',
  workdayStart: '09:00',
  workdayEnd: '18:00',
  allowWeekends: false,
  maxMeetingMinutes: 120,
  minBufferMinutes: 15,
  orgDomains: [],
  autoExecuteLowRisk: false,
  budgetApprovalLimit: 1000,
  orgCurrency: 'USD',
  approvalThresholds: {},
}
