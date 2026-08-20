"""Policy guardrails — SPEC-003 §4. Five rules encoding organisational intent."""

from __future__ import annotations

import re
from typing import Optional

from app.domain.payload import email_domain, to_email_list
from app.domain.timeutil import read_string
from app.domain.types import Rule, RuleContext, RuleViolation

_ALL = ("CALENDAR", "TASK", "EMAIL", "REMINDER")

_FINANCIAL = re.compile(
    r"\b(pay|payment|invoice|wire|transfer funds|purchase order|refund)\b", re.IGNORECASE
)
_EXPORTISH = re.compile(
    r"\b(export|download all|dump|extract the database|share the full transcript)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(don't|do not|no longer|cancel|skip|instead of|not going to)\b", re.IGNORECASE
)


def _is_internal(address: str, org_domains: list[str]) -> bool:
    domain = email_domain(address)
    if not domain:
        return False
    for entry in org_domains:
        clean = entry.strip().lower().lstrip("@")
        if clean and (domain == clean or domain.endswith(f".{clean}")):
            return True
    return False


def _pol_superseded(ctx: RuleContext) -> Optional[RuleViolation]:
    if not ctx.item.superseded_by_id:
        return None
    return RuleViolation(
        rule_id="POL_SUPERSEDED",
        severity="BLOCK",
        message="A later part of the conversation replaced this action.",
        remedy="Execute the replacement instead, or reject this one.",
    )


def _pol_external_email(ctx: RuleContext) -> Optional[RuleViolation]:
    # A draft reaches nobody, so the rule has nothing to say about it.
    if read_string(ctx.payload.get("sendMode")) != "send":
        return None
    recipients = (
        to_email_list(ctx.payload.get("to"))
        + to_email_list(ctx.payload.get("cc"))
        + to_email_list(ctx.payload.get("bcc"))
    )
    external = [r for r in recipients if not _is_internal(r, ctx.settings.org_domains)]
    if not external:
        return None
    if ctx.has_explicit_approval:
        return None
    return RuleViolation(
        rule_id="POL_EXTERNAL_EMAIL",
        severity="BLOCK",
        message=(
            f"Sending to external recipients ({', '.join(external)}) requires explicit approval."
        ),
        remedy="Approve this item, or switch the send mode to draft.",
    )


def _pol_no_financial_autoexec(ctx: RuleContext) -> Optional[RuleViolation]:
    if not _FINANCIAL.search(ctx.item.description):
        return None
    if ctx.has_explicit_approval:
        return None
    return RuleViolation(
        rule_id="POL_NO_FINANCIAL_AUTOEXEC",
        severity="BLOCK",
        message="Financial actions are never executed without a human decision.",
        remedy="Review the source quote, then approve explicitly.",
    )


def _pol_export_consent(ctx: RuleContext) -> Optional[RuleViolation]:
    if not _EXPORTISH.search(ctx.item.description):
        return None
    if ctx.payload.get("exportConsent") is True:
        return None
    return RuleViolation(
        rule_id="POL_EXPORT_CONSENT",
        severity="BLOCK",
        message="Data export requires recorded consent before it can run.",
        remedy="Record consent on this item, or handle the export manually.",
    )


def _pol_contradicts_decision(ctx: RuleContext) -> Optional[RuleViolation]:
    """Deliberately shallow: a negated restatement of a recorded decision.

    Real contradiction detection belongs to the extractor, which has the whole transcript.
    This is a cheap net for the obvious case, and it is a WARN precisely because it will
    sometimes be wrong.
    """
    if not _NEGATION.search(ctx.item.description):
        return None

    words = {w for w in re.split(r"[^a-z0-9]+", ctx.item.description.lower()) if len(w) > 4}
    for decision in ctx.decisions:
        decision_words = [
            w for w in re.split(r"[^a-z0-9]+", decision.statement.lower()) if len(w) > 4
        ]
        shared = [w for w in decision_words if w in words]
        if len(shared) >= 2:
            return RuleViolation(
                rule_id="POL_CONTRADICTS_DECISION",
                severity="WARN",
                message=(
                    f"May contradict a decision recorded in this meeting: “{decision.statement}”."
                ),
                remedy="Check the transcript around both moments before executing.",
            )
    return None


POL_SUPERSEDED = Rule("POL_SUPERSEDED", "Not superseded", "BLOCK", _ALL, _pol_superseded)
POL_EXTERNAL_EMAIL = Rule(
    "POL_EXTERNAL_EMAIL", "External email needs approval", "BLOCK", ("EMAIL",), _pol_external_email
)
POL_NO_FINANCIAL_AUTOEXEC = Rule(
    "POL_NO_FINANCIAL_AUTOEXEC", "No financial auto-execution", "BLOCK", _ALL,
    _pol_no_financial_autoexec,
)
POL_EXPORT_CONSENT = Rule(
    "POL_EXPORT_CONSENT", "Export needs consent", "BLOCK", _ALL, _pol_export_consent
)
POL_CONTRADICTS_DECISION = Rule(
    "POL_CONTRADICTS_DECISION", "Does not contradict a decision", "WARN", _ALL,
    _pol_contradicts_decision,
)

POLICY_RULES: tuple[Rule, ...] = (
    POL_SUPERSEDED,
    POL_EXTERNAL_EMAIL,
    POL_NO_FINANCIAL_AUTOEXEC,
    POL_EXPORT_CONSENT,
    POL_CONTRADICTS_DECISION,
)
