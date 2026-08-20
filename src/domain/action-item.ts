import { missingFields } from './payload'
import type { ActionItemCore, ActionStatus, Priority, Readiness, RuleViolation } from './types'

/**
 * Pure derivations over an action item — readiness, grouping, ordering, and the
 * status state machine. No I/O, no clock reads except where `now` is passed in.
 */

// ─────────────────────────────────────────────── status state machine (§5) ──

/** SPEC-001 §5. Absent key ⇒ terminal. */
const TRANSITIONS: Record<ActionStatus, readonly ActionStatus[]> = {
  PROPOSED: ['APPROVED', 'REJECTED', 'DEFERRED'],
  DEFERRED: ['APPROVED', 'REJECTED', 'PROPOSED'],
  APPROVED: ['EXECUTING', 'REJECTED', 'PROPOSED'],
  EXECUTING: ['EXECUTED', 'FAILED'],
  FAILED: ['EXECUTING', 'REJECTED'],
  EXECUTED: [],
  REJECTED: ['PROPOSED'],
}

/** Statuses only the executor may assign — a client PATCH naming one is a 422. */
export const SERVER_ONLY_STATUSES: readonly ActionStatus[] = ['EXECUTING', 'EXECUTED']

export function canTransition(from: ActionStatus, to: ActionStatus): boolean {
  return (TRANSITIONS[from] ?? []).includes(to)
}

export function transitionError(from: ActionStatus, to: ActionStatus): string | null {
  if (from === to) return null
  if (from === 'EXECUTED') {
    // The side effect exists in the world; a status edit would be a lie.
    return 'This action has already been executed. Undo it with a compensating action instead.'
  }
  if (!canTransition(from, to)) return `Cannot move an action from ${from} to ${to}.`
  return null
}

export const AUDIT_EVENT_FOR_STATUS: Partial<Record<ActionStatus, string>> = {
  APPROVED: 'action_item.approved',
  REJECTED: 'action_item.rejected',
  DEFERRED: 'action_item.deferred',
  PROPOSED: 'action_item.reopened',
}

// ──────────────────────────────────────────────────────── readiness (§6) ──

export interface ReadinessResult {
  readiness: Readiness
  missingFields: string[]
  /** Human-readable reasons, in the order the UI should show them. */
  reasons: string[]
}

/**
 * First match wins, in the order given by SPEC-001 §6. Ordering matters: an item
 * with nothing to execute is INFORMATIONAL even if it is also missing fields,
 * because telling a reviewer to "fix" an item that will never execute is noise.
 */
export function computeReadiness(
  item: ActionItemCore,
  violations: RuleViolation[] = [],
): ReadinessResult {
  if (item.actionType === 'NONE' || item.status === 'REJECTED') {
    return {
      readiness: 'INFORMATIONAL',
      missingFields: [],
      reasons: item.status === 'REJECTED' ? ['Rejected by a reviewer.'] : ['No executable action.'],
    }
  }

  const missing = missingFields(item.actionType, item.payload)
  const blocking = violations.filter((v) => v.severity === 'BLOCK')
  const reasons: string[] = []

  if (missing.length > 0) reasons.push(`Missing required detail: ${missing.join(', ')}.`)
  if (item.confidence === 'LOW') reasons.push('The extractor had low confidence in this item.')
  if (!item.ownerName) reasons.push('No owner identified.')
  for (const v of blocking) reasons.push(v.message)

  return {
    readiness: reasons.length > 0 ? 'NEEDS_CLARIFICATION' : 'READY',
    missingFields: missing,
    reasons,
  }
}

/** Items in flight or done are shown in their own lane, not the decision groups. */
export function isInExecutionLane(status: ActionStatus): boolean {
  return status === 'EXECUTING' || status === 'EXECUTED' || status === 'FAILED'
}

export const GROUP_ORDER: readonly Readiness[] = ['READY', 'NEEDS_CLARIFICATION', 'INFORMATIONAL']

export const GROUP_META: Record<Readiness, { title: string; blurb: string; icon: string }> = {
  READY: {
    title: 'Ready to Execute',
    blurb: 'Complete, owned, and passing every guardrail.',
    icon: 'bi-lightning-charge-fill',
  },
  NEEDS_CLARIFICATION: {
    title: 'Needs Clarification',
    blurb: 'Missing a detail, low confidence, or blocked by a rule.',
    icon: 'bi-question-circle-fill',
  },
  INFORMATIONAL: {
    title: 'Informational',
    blurb: 'Nothing to execute — kept for the record.',
    icon: 'bi-info-circle-fill',
  },
}

// ───────────────────────────────────────────────────────── ordering (§8) ──

const PRIORITY_RANK: Record<Priority, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

/**
 * Fixed order: priority desc → deadline asc (nulls last) → source timestamp asc.
 * Not user-configurable on purpose — a stable order makes "work top to bottom"
 * correct, and reviewers lose their place when the order shifts under them.
 */
export function compareForReview(a: ActionItemCore, b: ActionItemCore): number {
  const byPriority = PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority]
  if (byPriority !== 0) return byPriority

  const aTime = a.deadline?.getTime() ?? Number.POSITIVE_INFINITY
  const bTime = b.deadline?.getTime() ?? Number.POSITIVE_INFINITY
  if (aTime !== bTime) return aTime - bTime

  return (a.sourceTimestampMs ?? 0) - (b.sourceTimestampMs ?? 0)
}

export function groupByReadiness<T extends { readiness: Readiness }>(
  items: T[],
): Record<Readiness, T[]> {
  const groups: Record<Readiness, T[]> = { READY: [], NEEDS_CLARIFICATION: [], INFORMATIONAL: [] }
  for (const item of items) groups[item.readiness].push(item)
  return groups
}
