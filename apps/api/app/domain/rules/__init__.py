"""The guardrail engine — a port of ``src/domain/rules/``.

17 rules in three families: scheduling (7), validation (5), policy (5). Every rule is a
pure function of ``RuleContext``, which is what lets them be tested against a fixed instant
with no database and no clock.

Two engine properties matter more than any individual rule:

* **A rule that throws does not stop the evaluation.** It becomes an INFO violation naming
  itself, and the others still run. An engine that died on one malformed payload would take
  every *other* guardrail down with it — the opposite of what it exists for.
* **BLOCK sorts before WARN before INFO.** The reviewer reads the reason they cannot
  proceed first, rather than scanning past three warnings to find it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.types import Rule, RuleContext, RuleViolation

from .policy import POLICY_RULES
from .scheduling import SCHEDULING_RULES
from .validation import VALIDATION_RULES

RULES: tuple[Rule, ...] = (*SCHEDULING_RULES, *VALIDATION_RULES, *POLICY_RULES)

_SEVERITY_ORDER = {"BLOCK": 0, "WARN": 1, "INFO": 2}


@dataclass(frozen=True)
class RuleEvaluation:
    violations: list[RuleViolation]
    blocking: list[RuleViolation]
    warnings: list[RuleViolation]
    passes: bool


def evaluate_rules(ctx: RuleContext, rules: tuple[Rule, ...] = RULES) -> RuleEvaluation:
    violations: list[RuleViolation] = []
    for rule in rules:
        if ctx.item.action_type not in rule.applies_to:
            continue
        try:
            result = rule.evaluate(ctx)
        except Exception as exc:  # noqa: BLE001 — see the module note
            violations.append(
                RuleViolation(
                    rule_id=rule.id,
                    severity="INFO",
                    message=f"Guardrail {rule.id} could not be evaluated: {exc}",
                )
            )
            continue
        if result:
            violations.append(result)

    violations.sort(key=lambda v: _SEVERITY_ORDER.get(v.severity, 3))
    blocking = [v for v in violations if v.severity == "BLOCK"]
    return RuleEvaluation(
        violations=violations,
        blocking=blocking,
        warnings=[v for v in violations if v.severity == "WARN"],
        passes=not blocking,
    )


__all__ = [
    "RULES",
    "POLICY_RULES",
    "SCHEDULING_RULES",
    "VALIDATION_RULES",
    "RuleEvaluation",
    "evaluate_rules",
]
