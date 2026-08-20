import { emailDomain, toEmailList } from './payload'
import type {
  ActionItemCore,
  ApprovalGate,
  Confidence,
  RiskTier,
  SettingsView,
} from './types'

/**
 * Risk classification and the approval gate — SPEC-003 §3 and §5.
 *
 * Escalation only: `raise()` can move a tier up and never down, so no ordering of
 * signals can accidentally downgrade a dangerous action. Risk is computed on
 * every evaluation rather than stored, because it is a function of the *current*
 * payload — an item that becomes external when a recipient is added must become
 * HIGH at that moment, not stay MEDIUM because that is what was saved.
 */

const RANK: Record<RiskTier, number> = { LOW: 0, MEDIUM: 1, HIGH: 2 }

function raise(current: RiskTier, candidate: RiskTier): RiskTier {
  return RANK[candidate] > RANK[current] ? candidate : current
}

const DESTRUCTIVE = /\b(delete|remove|wipe|revoke|terminate|drop|purge|deactivate|cancel)\b/i
const MONEY = /(?:[$€£₹]\s?|(?:usd|eur|gbp|inr)\s?)([\d,]+(?:\.\d+)?)\s?(k|m|thousand|million)?/gi

/** Largest money amount mentioned, normalised. Null when nothing looks monetary. */
export function largestAmount(text: string): number | null {
  let max: number | null = null
  for (const match of text.matchAll(MONEY)) {
    const base = Number((match[1] ?? '').replace(/,/g, ''))
    if (!Number.isFinite(base)) continue
    const suffix = (match[2] ?? '').toLowerCase()
    const scale = suffix === 'k' || suffix === 'thousand' ? 1_000
      : suffix === 'm' || suffix === 'million' ? 1_000_000
      : 1
    const value = base * scale
    if (max === null || value > max) max = value
  }
  return max
}

export interface RiskAssessment {
  tier: RiskTier
  /** Why the tier is what it is — shown in the confirmation modal. */
  factors: string[]
}

function isInternal(address: string, orgDomains: string[]): boolean {
  const domain = emailDomain(address)
  if (!domain) return false
  return orgDomains.some((d) => {
    const clean = d.trim().toLowerCase().replace(/^@/, '')
    return clean.length > 0 && (domain === clean || domain.endsWith(`.${clean}`))
  })
}

export function classifyRisk(
  item: ActionItemCore,
  payload: Record<string, unknown>,
  settings: SettingsView,
  selfEmail: string | null,
): RiskAssessment {
  let tier: RiskTier = 'LOW'
  const factors: string[] = []
  const self = selfEmail?.toLowerCase() ?? null
  const org = settings.orgDomains

  switch (item.actionType) {
    case 'EMAIL': {
      const recipients = [...toEmailList(payload.to), ...toEmailList(payload.cc), ...toEmailList(payload.bcc)]
      const sendMode = typeof payload.sendMode === 'string' ? payload.sendMode : 'draft'
      if (sendMode === 'draft') {
        factors.push('Saved as a draft — nothing is sent until you send it.')
      } else {
        const external = recipients.filter((r) => !isInternal(r, org) && r !== self)
        if (external.length > 0) {
          tier = raise(tier, 'HIGH')
          factors.push(`Sends outside the organisation: ${external.join(', ')}.`)
        } else {
          tier = raise(tier, 'MEDIUM')
          factors.push(`Sends to ${recipients.length} internal recipient(s).`)
        }
      }
      break
    }
    case 'CALENDAR': {
      const attendees = toEmailList(payload.attendees).filter((a) => a !== self)
      if (attendees.length === 0) {
        factors.push('Only blocks time on your own calendar.')
      } else {
        tier = raise(tier, 'MEDIUM')
        factors.push(`Invites ${attendees.length} other ${attendees.length === 1 ? 'person' : 'people'}.`)
      }
      break
    }
    case 'TASK': {
      const assignee = typeof payload.assignee === 'string' ? payload.assignee.toLowerCase() : ''
      if (!assignee || assignee === self) {
        factors.push('Creates a task for you only.')
      } else {
        tier = raise(tier, 'MEDIUM')
        factors.push(`Assigns work to ${payload.assignee as string}.`)
      }
      break
    }
    case 'REMINDER': {
      const channel = typeof payload.channel === 'string' ? payload.channel : 'self'
      if (channel === 'self') {
        factors.push('Reminds you only.')
      } else {
        tier = raise(tier, 'MEDIUM')
        factors.push(`Posts a reminder to ${channel}.`)
      }
      break
    }
    case 'NONE':
      break
  }

  // Cross-cutting escalations apply to every action type.
  const haystack = `${item.description} ${item.sourceQuote ?? ''}`
  const amount = largestAmount(haystack)
  if (amount !== null && amount > settings.budgetApprovalLimit) {
    tier = raise(tier, 'HIGH')
    factors.push(
      `Mentions ${settings.orgCurrency} ${amount.toLocaleString('en-US')}, above the ` +
        `${settings.orgCurrency} ${settings.budgetApprovalLimit.toLocaleString('en-US')} approval limit.`,
    )
  }
  if (DESTRUCTIVE.test(item.description)) {
    tier = raise(tier, 'HIGH')
    factors.push('Describes a destructive or irreversible operation.')
  }

  return { tier, factors }
}

/** SPEC-003 §5. A per-type threshold in settings may only tighten the gate. */
export function effectiveGate(
  tier: RiskTier,
  confidence: Confidence,
  settings: SettingsView,
  actionType: ActionItemCore['actionType'],
): { gate: ApprovalGate; note: string | null } {
  let gate: ApprovalGate
  let note: string | null = null

  if (tier === 'HIGH') {
    gate = 'EXPLICIT_APPROVAL_WITH_CONFIRMATION'
  } else if (tier === 'MEDIUM') {
    gate = 'EXPLICIT_APPROVAL'
  } else if (confidence === 'HIGH' && settings.autoExecuteLowRisk) {
    gate = 'AUTO'
  } else {
    gate = 'EXPLICIT_APPROVAL'
    if (confidence === 'LOW') {
      note = 'The extractor was unsure about this item — check the source quote before approving.'
    }
  }

  const configured = settings.approvalThresholds?.[actionType]
  if (configured && GATE_RANK[configured] > GATE_RANK[gate]) gate = configured

  return { gate, note }
}

const GATE_RANK: Record<ApprovalGate, number> = {
  AUTO: 0,
  EXPLICIT_APPROVAL: 1,
  EXPLICIT_APPROVAL_WITH_CONFIRMATION: 2,
}

export function gateSatisfied(gate: ApprovalGate, status: ActionItemCore['status']): boolean {
  if (gate === 'AUTO') return true
  // Both approval gates require a human decision recorded on the row itself.
  // FAILED is included so a retry after a transient provider error does not
  // require re-approving something the reviewer already approved.
  return status === 'APPROVED' || status === 'FAILED'
}

export const RISK_META: Record<RiskTier, { label: string; blurb: string }> = {
  LOW: { label: 'Low risk', blurb: 'Reversible and private to you.' },
  MEDIUM: { label: 'Medium risk', blurb: 'Visible to colleagues; awkward to undo.' },
  HIGH: { label: 'High risk', blurb: 'Reaches outside the org or destroys data.' },
}
