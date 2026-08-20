import type { Rule, RuleContext, RuleViolation, Severity } from '../types'
import { POLICY_RULES } from './policy'
import { SCHEDULING_RULES } from './scheduling'
import { VALIDATION_RULES } from './validation'

/**
 * The rule registry and engine — SPEC-003 §2.
 *
 * Adding a guardrail is one file plus one line here. No other layer changes,
 * which is the property an auditor will probe: the envelope is enumerable.
 */
export const RULES: readonly Rule[] = [...SCHEDULING_RULES, ...VALIDATION_RULES, ...POLICY_RULES]

const SEVERITY_ORDER: Record<Severity, number> = { BLOCK: 0, WARN: 1, INFO: 2 }

export interface RuleEvaluation {
  violations: RuleViolation[]
  blocking: RuleViolation[]
  warnings: RuleViolation[]
  /** True when nothing prevents execution. Warnings do not prevent it. */
  passes: boolean
}

/**
 * Pure. A rule that throws is contained and reported as an INFO note rather than
 * failing the whole evaluation — a bug in one guardrail must not disable the
 * other twelve, which is the failure mode that turns a safety net into a hazard.
 */
export function evaluateRules(ctx: RuleContext, rules: readonly Rule[] = RULES): RuleEvaluation {
  const violations: RuleViolation[] = []

  for (const rule of rules) {
    if (!rule.appliesTo.includes(ctx.item.actionType)) continue
    try {
      const result = rule.evaluate(ctx)
      if (result) violations.push(result)
    } catch (err) {
      violations.push({
        ruleId: rule.id,
        severity: 'INFO',
        message: `Guardrail ${rule.id} could not be evaluated: ${(err as Error).message}`,
      })
    }
  }

  violations.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])

  const blocking = violations.filter((v) => v.severity === 'BLOCK')
  return {
    violations,
    blocking,
    warnings: violations.filter((v) => v.severity === 'WARN'),
    passes: blocking.length === 0,
  }
}

export { SCHEDULING_RULES, VALIDATION_RULES, POLICY_RULES }
