import { fieldLabel, isValidEmail, missingFields, toEmailList } from '../payload'
import { largestAmount } from '../risk'
import type { Rule } from '../types'
import { humanTime, readDate } from './helpers'

/** Validation guardrails — SPEC-003 §4. */

const ALL = ['CALENDAR', 'TASK', 'EMAIL', 'REMINDER'] as const

export const VAL_REQUIRED_FIELDS: Rule = {
  id: 'VAL_REQUIRED_FIELDS',
  appliesTo: [...ALL],
  severity: 'BLOCK',
  evaluate(ctx) {
    const missing = missingFields(ctx.item.actionType, ctx.payload)
    if (missing.length === 0) return null
    const labels = missing.map((k) => fieldLabel(ctx.item.actionType, k))
    return {
      ruleId: 'VAL_REQUIRED_FIELDS',
      severity: 'BLOCK',
      field: missing[0],
      message: `Missing required ${missing.length === 1 ? 'detail' : 'details'}: ${labels.join(', ')}.`,
      remedy: 'Fill these in with Edit before executing.',
    }
  },
}

export const VAL_DEADLINE_PAST: Rule = {
  id: 'VAL_DEADLINE_PAST',
  appliesTo: [...ALL],
  severity: 'BLOCK',
  evaluate(ctx) {
    // The item's own deadline is advisory metadata; the payload's dueAt is what
    // gets written to a provider, so only that one blocks execution.
    const dueAt = readDate(ctx.payload.dueAt)
    if (!dueAt) return null
    if (dueAt.getTime() >= ctx.now.getTime()) return null
    return {
      ruleId: 'VAL_DEADLINE_PAST',
      severity: 'BLOCK',
      field: 'dueAt',
      message: `Due ${humanTime(dueAt, ctx.settings.timeZone)}, which has already passed.`,
      remedy: 'Set a future due date.',
    }
  },
}

export const VAL_EMAIL_FORMAT: Rule = {
  id: 'VAL_EMAIL_FORMAT',
  appliesTo: ['EMAIL', 'CALENDAR'],
  severity: 'BLOCK',
  evaluate(ctx) {
    const keys = ctx.item.actionType === 'EMAIL' ? ['to', 'cc', 'bcc'] : ['attendees']
    const invalid: string[] = []
    for (const key of keys) {
      for (const address of toEmailList(ctx.payload[key])) {
        if (!isValidEmail(address)) invalid.push(address)
      }
    }
    if (invalid.length === 0) return null
    return {
      ruleId: 'VAL_EMAIL_FORMAT',
      severity: 'BLOCK',
      field: keys[0],
      message: `Not a valid email address: ${invalid.join(', ')}.`,
      remedy: 'Correct or remove the address.',
    }
  },
}

export const VAL_BUDGET_APPROVAL: Rule = {
  id: 'VAL_BUDGET_APPROVAL',
  appliesTo: [...ALL],
  severity: 'BLOCK',
  evaluate(ctx) {
    const amount = largestAmount(`${ctx.item.description} ${ctx.item.sourceQuote ?? ''}`)
    const limit = ctx.settings.budgetApprovalLimit
    if (amount === null || amount <= limit) return null
    if (ctx.payload.managerApproved === true) return null
    const cur = ctx.settings.orgCurrency
    return {
      ruleId: 'VAL_BUDGET_APPROVAL',
      severity: 'BLOCK',
      message:
        `Involves ${cur} ${amount.toLocaleString('en-US')}, above the ` +
        `${cur} ${limit.toLocaleString('en-US')} threshold that needs a manager's sign-off.`,
      remedy: 'Record manager approval on this item before executing.',
    }
  },
}

export const VALIDATION_RULES: readonly Rule[] = [
  VAL_REQUIRED_FIELDS,
  VAL_DEADLINE_PAST,
  VAL_EMAIL_FORMAT,
  VAL_BUDGET_APPROVAL,
]
