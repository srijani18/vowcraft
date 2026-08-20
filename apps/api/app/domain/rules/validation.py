"""Validation guardrails — SPEC-003 §3. Five rules about whether the data is usable."""

from __future__ import annotations

import re
from typing import Optional

from app.domain.payload import field_label, is_valid_email, missing_fields, to_email_list
from app.domain.risk import largest_amount
from app.domain.timeutil import human_time, read_date
from app.domain.types import Rule, RuleContext, RuleViolation

_ALL = ("CALENDAR", "TASK", "EMAIL", "REMINDER")


def _format_amount(value: float) -> str:
    return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"


def _val_required_fields(ctx: RuleContext) -> Optional[RuleViolation]:
    missing = missing_fields(ctx.item.action_type, ctx.payload)
    if not missing:
        return None
    labels = [field_label(ctx.item.action_type, key) for key in missing]
    noun = "detail" if len(labels) == 1 else "details"
    return RuleViolation(
        rule_id="VAL_REQUIRED_FIELDS",
        severity="BLOCK",
        message=f"Missing required {noun}: {', '.join(labels)}.",
        remedy="Fill them in, or reject the item if the meeting never said.",
    )


def _val_deadline_past(ctx: RuleContext) -> Optional[RuleViolation]:
    due_at = read_date(ctx.payload.get("dueAt"))
    if not due_at:
        return None
    if due_at >= ctx.now:
        return None
    return RuleViolation(
        rule_id="VAL_DEADLINE_PAST",
        severity="BLOCK",
        message=f"Due {human_time(due_at, ctx.settings.time_zone)}, which has already passed.",
        remedy="Set a future due date.",
    )


def _val_email_format(ctx: RuleContext) -> Optional[RuleViolation]:
    keys = ("to", "cc", "bcc") if ctx.item.action_type == "EMAIL" else ("attendees",)
    invalid: list[str] = []
    for key in keys:
        for address in to_email_list(ctx.payload.get(key)):
            if not is_valid_email(address):
                invalid.append(address)
    if not invalid:
        return None
    return RuleViolation(
        rule_id="VAL_EMAIL_FORMAT",
        severity="BLOCK",
        message=f"Not a valid email address: {', '.join(invalid)}.",
        remedy="Correct or remove the address.",
    )


def _val_owner_known(ctx: RuleContext) -> Optional[RuleViolation]:
    owner = (ctx.item.owner_name or "").strip()
    # Absence is readiness' problem, not this rule's. Reporting "no owner" here as well
    # would double up on the same gap.
    if not owner:
        return None
    owner_email = (ctx.item.owner_email or "").lower()
    known = any(
        m.name.lower() == owner.lower() or (owner_email and m.email.lower() == owner_email)
        for m in ctx.team_members
    )
    if known:
        return None
    return RuleViolation(
        rule_id="VAL_OWNER_KNOWN",
        # WARN: a name missing from the roster is usually an incomplete roster, not a
        # wrong assignment. Blocking would punish the common case.
        severity="WARN",
        message=(
            f"“{owner}” is not in your team roster, so this may be assigned to the wrong person."
        ),
        remedy="Add them to the roster, or correct the owner.",
    )


def _val_budget_approval(ctx: RuleContext) -> Optional[RuleViolation]:
    amount = largest_amount(f"{ctx.item.description} {ctx.item.source_quote or ''}")
    limit = ctx.settings.budget_approval_limit
    if amount is None or amount <= limit:
        return None
    # An explicit manager sign-off recorded on the item clears it.
    if ctx.payload.get("managerApproved") is True:
        return None
    cur = ctx.settings.org_currency
    return RuleViolation(
        rule_id="VAL_BUDGET_APPROVAL",
        severity="BLOCK",
        message=(
            f"Involves {cur} {_format_amount(amount)}, above the "
            f"{cur} {_format_amount(float(limit))} threshold that needs a manager's sign-off."
        ),
        remedy="Record manager approval on this item before executing.",
    )


VAL_REQUIRED_FIELDS = Rule(
    "VAL_REQUIRED_FIELDS", "All required fields present", "BLOCK", _ALL, _val_required_fields
)
VAL_DEADLINE_PAST = Rule(
    "VAL_DEADLINE_PAST", "Deadline in the future", "BLOCK", _ALL, _val_deadline_past
)
VAL_EMAIL_FORMAT = Rule(
    "VAL_EMAIL_FORMAT", "Addresses well-formed", "BLOCK", ("EMAIL", "CALENDAR"), _val_email_format
)
VAL_OWNER_KNOWN = Rule("VAL_OWNER_KNOWN", "Owner is on the roster", "WARN", _ALL, _val_owner_known)
VAL_BUDGET_APPROVAL = Rule(
    "VAL_BUDGET_APPROVAL", "Budget within limit", "BLOCK", _ALL, _val_budget_approval
)

VALIDATION_RULES: tuple[Rule, ...] = (
    VAL_REQUIRED_FIELDS,
    VAL_DEADLINE_PAST,
    VAL_EMAIL_FORMAT,
    VAL_OWNER_KNOWN,
    VAL_BUDGET_APPROVAL,
)
