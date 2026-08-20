import { emailDomain, toEmailList } from '../payload'
import type { Rule } from '../types'
import { readString } from './helpers'

/** Policy guardrails — SPEC-003 §4. These encode organisational intent. */

const ALL = ['CALENDAR', 'TASK', 'EMAIL', 'REMINDER'] as const

function isInternal(address: string, orgDomains: string[]): boolean {
  const domain = emailDomain(address)
  if (!domain) return false
  return orgDomains.some((d) => {
    const clean = d.trim().toLowerCase().replace(/^@/, '')
    return clean.length > 0 && (domain === clean || domain.endsWith(`.${clean}`))
  })
}

export const POL_EXTERNAL_EMAIL: Rule = {
  id: 'POL_EXTERNAL_EMAIL',
  appliesTo: ['EMAIL'],
  severity: 'BLOCK',
  evaluate(ctx) {
    if (readString(ctx.payload.sendMode) !== 'send') return null
    const recipients = [
      ...toEmailList(ctx.payload.to),
      ...toEmailList(ctx.payload.cc),
      ...toEmailList(ctx.payload.bcc),
    ]
    const external = recipients.filter((r) => !isInternal(r, ctx.settings.orgDomains))
    if (external.length === 0) return null
    // The gate is the human decision, not the rule: once explicitly approved
    // this stops blocking, which is exactly what "requires approval" means.
    if (ctx.hasExplicitApproval) return null
    return {
      ruleId: 'POL_EXTERNAL_EMAIL',
      severity: 'BLOCK',
      field: 'to',
      message: `Sending to external recipients (${external.join(', ')}) requires explicit approval.`,
      remedy: 'Approve this item, or switch the send mode to draft.',
    }
  },
}

export const POL_NO_FINANCIAL_AUTOEXEC: Rule = {
  id: 'POL_NO_FINANCIAL_AUTOEXEC',
  appliesTo: [...ALL],
  severity: 'BLOCK',
  evaluate(ctx) {
    const financial = /\b(pay|payment|invoice|wire|transfer funds|purchase order|refund)\b/i
    if (!financial.test(ctx.item.description)) return null
    if (ctx.hasExplicitApproval) return null
    return {
      ruleId: 'POL_NO_FINANCIAL_AUTOEXEC',
      severity: 'BLOCK',
      message: 'Financial actions are never executed without a human decision.',
      remedy: 'Review the source quote, then approve explicitly.',
    }
  },
}

export const POL_EXPORT_CONSENT: Rule = {
  id: 'POL_EXPORT_CONSENT',
  appliesTo: [...ALL],
  severity: 'BLOCK',
  evaluate(ctx) {
    const exportish = /\b(export|download all|dump|extract the database|share the full transcript)\b/i
    if (!exportish.test(ctx.item.description)) return null
    if (ctx.payload.exportConsent === true) return null
    return {
      ruleId: 'POL_EXPORT_CONSENT',
      severity: 'BLOCK',
      message: 'Data export requires recorded consent before it can run.',
      remedy: 'Record consent on this item, or handle the export manually.',
    }
  },
}

export const POL_SUPERSEDED: Rule = {
  id: 'POL_SUPERSEDED',
  appliesTo: [...ALL],
  severity: 'BLOCK',
  evaluate(ctx) {
    if (!ctx.item.supersededById) return null
    return {
      ruleId: 'POL_SUPERSEDED',
      severity: 'BLOCK',
      message: 'A later part of the conversation replaced this action.',
      remedy: 'Execute the replacement instead, or reject this one.',
    }
  },
}

export const POL_CONTRADICTS_DECISION: Rule = {
  id: 'POL_CONTRADICTS_DECISION',
  appliesTo: [...ALL],
  severity: 'WARN',
  evaluate(ctx) {
    // Deliberately shallow: a negated restatement of a recorded decision. Real
    // contradiction detection belongs to the extractor, which has the whole
    // transcript; this is a cheap net for the obvious case.
    const negation = /\b(don't|do not|no longer|cancel|skip|instead of|not going to)\b/i
    if (!negation.test(ctx.item.description)) return null

    const words = new Set(
      ctx.item.description
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .filter((w) => w.length > 4),
    )
    const clash = ctx.decisions.find((d) => {
      const decisionWords = d.statement
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .filter((w) => w.length > 4)
      const shared = decisionWords.filter((w) => words.has(w))
      return shared.length >= 2
    })
    if (!clash) return null

    return {
      ruleId: 'POL_CONTRADICTS_DECISION',
      severity: 'WARN',
      message: `May contradict a decision recorded in this meeting: “${clash.statement}”.`,
      remedy: 'Check the transcript around both moments before executing.',
    }
  },
}

export const POLICY_RULES: readonly Rule[] = [
  POL_SUPERSEDED,
  POL_EXTERNAL_EMAIL,
  POL_NO_FINANCIAL_AUTOEXEC,
  POL_EXPORT_CONSENT,
  POL_CONTRADICTS_DECISION,
]
