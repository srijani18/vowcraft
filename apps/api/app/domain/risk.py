"""Risk classification and the approval gate — a port of ``src/domain/risk.ts``.

Two properties are load-bearing and preserved exactly:

* **Escalation only.** ``_raise`` never lowers a tier. Every factor can push risk up and
  none can pull it down, so no ordering of checks can accidentally downgrade something
  dangerous — and a new check cannot make an existing item *less* guarded.
* **A configured threshold can only tighten.** If settings ask for a stricter gate than
  the computed one, the stricter wins; if they ask for a looser one, it is ignored. A
  configuration mistake therefore cannot open a hole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.domain.payload import email_domain, to_email_list
from app.domain.types import ActionItemCore, ApprovalGate, Confidence, RiskTier, SettingsView

_RANK: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
_GATE_RANK: dict[str, int] = {
    "AUTO": 0,
    "EXPLICIT_APPROVAL": 1,
    "EXPLICIT_APPROVAL_WITH_CONFIRMATION": 2,
}


def _raise(current: RiskTier, candidate: RiskTier) -> RiskTier:
    return candidate if _RANK[candidate] > _RANK[current] else current


_DESTRUCTIVE = re.compile(
    r"\b(delete|remove|wipe|revoke|terminate|drop|purge|deactivate|cancel)\b", re.IGNORECASE
)
_MONEY = re.compile(
    r"(?:[$€£₹]\s?|(?:usd|eur|gbp|inr)\s?)([\d,]+(?:\.\d+)?)\s?(k|m|thousand|million)?",
    re.IGNORECASE,
)


def largest_amount(text: str) -> Optional[float]:
    """The largest money figure mentioned, scaled for k/m suffixes.

    The *largest*, not the first: "reduce the £200 fee on the £2,000,000 contract" is a
    two-million-pound conversation, and taking the first match would classify it as
    trivial.
    """
    largest: Optional[float] = None
    for match in _MONEY.finditer(text):
        raw = (match.group(1) or "").replace(",", "")
        try:
            base = float(raw)
        except ValueError:
            continue
        suffix = (match.group(2) or "").lower()
        scale = 1_000 if suffix in ("k", "thousand") else 1_000_000 if suffix in ("m", "million") else 1
        value = base * scale
        if largest is None or value > largest:
            largest = value
    return largest


@dataclass(frozen=True)
class RiskAssessment:
    tier: RiskTier
    #: Written for a reviewer, in order of discovery. This is the "why" shown next to the
    #: tier badge — a tier with no explanation is just a colour.
    factors: list[str]


def _is_internal(address: str, org_domains: list[str]) -> bool:
    domain = email_domain(address)
    if not domain:
        return False
    for entry in org_domains:
        clean = entry.strip().lower().lstrip("@")
        # Subdomains count as internal: mail.acme.com belongs to acme.com.
        if clean and (domain == clean or domain.endswith(f".{clean}")):
            return True
    return False


def _format_amount(value: float) -> str:
    return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"


def classify_risk(
    item: ActionItemCore,
    payload: dict[str, Any],
    settings: SettingsView,
    self_email: Optional[str],
) -> RiskAssessment:
    tier: RiskTier = "LOW"
    factors: list[str] = []
    self_addr = self_email.lower() if self_email else None
    org = settings.org_domains

    if item.action_type == "EMAIL":
        recipients = (
            to_email_list(payload.get("to"))
            + to_email_list(payload.get("cc"))
            + to_email_list(payload.get("bcc"))
        )
        send_mode = payload.get("sendMode") if isinstance(payload.get("sendMode"), str) else "draft"
        if send_mode == "draft":
            # A draft reaches nobody, so it is genuinely low risk however alarming the
            # recipient list looks. Saying so stops a reviewer over-thinking it.
            factors.append("Saved as a draft — nothing is sent until you send it.")
        else:
            external = [r for r in recipients if not _is_internal(r, org) and r != self_addr]
            if external:
                tier = _raise(tier, "HIGH")
                factors.append(f"Sends outside the organisation: {', '.join(external)}.")
            else:
                tier = _raise(tier, "MEDIUM")
                factors.append(f"Sends to {len(recipients)} internal recipient(s).")

    elif item.action_type == "CALENDAR":
        attendees = [a for a in to_email_list(payload.get("attendees")) if a != self_addr]
        if not attendees:
            factors.append("Only blocks time on your own calendar.")
        else:
            tier = _raise(tier, "MEDIUM")
            noun = "person" if len(attendees) == 1 else "people"
            factors.append(f"Invites {len(attendees)} other {noun}.")

    elif item.action_type == "TASK":
        assignee = payload.get("assignee")
        assignee_lower = assignee.lower() if isinstance(assignee, str) else ""
        if not assignee_lower or assignee_lower == self_addr:
            factors.append("Creates a task for you only.")
        else:
            tier = _raise(tier, "MEDIUM")
            factors.append(f"Assigns work to {assignee}.")

    elif item.action_type == "REMINDER":
        channel = payload.get("channel") if isinstance(payload.get("channel"), str) else "self"
        if channel == "self":
            factors.append("Reminds you only.")
        else:
            tier = _raise(tier, "MEDIUM")
            factors.append(f"Posts a reminder to {channel}.")

    # Money and destructive language are read from the *description and quote*, not the
    # payload: the amount is usually stated in the conversation and never makes it into a
    # structured field.
    haystack = f"{item.description} {item.source_quote or ''}"
    amount = largest_amount(haystack)
    if amount is not None and amount > settings.budget_approval_limit:
        tier = _raise(tier, "HIGH")
        factors.append(
            f"Mentions {settings.org_currency} {_format_amount(amount)}, above the "
            f"{settings.org_currency} {_format_amount(float(settings.budget_approval_limit))} "
            "approval limit."
        )

    if _DESTRUCTIVE.search(item.description):
        tier = _raise(tier, "HIGH")
        factors.append("Describes a destructive or irreversible operation.")

    return RiskAssessment(tier=tier, factors=factors)


@dataclass(frozen=True)
class GateDecision:
    gate: ApprovalGate
    note: Optional[str]


def effective_gate(
    tier: RiskTier, confidence: Confidence, settings: SettingsView, action_type: str
) -> GateDecision:
    note: Optional[str] = None
    if tier == "HIGH":
        gate: ApprovalGate = "EXPLICIT_APPROVAL_WITH_CONFIRMATION"
    elif tier == "MEDIUM":
        gate = "EXPLICIT_APPROVAL"
    elif confidence == "HIGH" and settings.auto_execute_low_risk:
        gate = "AUTO"
    else:
        gate = "EXPLICIT_APPROVAL"
        if confidence == "LOW":
            note = (
                "The extractor was unsure about this item — check the source quote before "
                "approving."
            )

    configured = (settings.approval_thresholds or {}).get(action_type)
    # Tighten only. A settings value asking for a *looser* gate than the risk warrants is
    # ignored, so a configuration mistake cannot open a hole.
    if configured and _GATE_RANK.get(configured, 0) > _GATE_RANK[gate]:
        gate = configured  # type: ignore[assignment]
    return GateDecision(gate=gate, note=note)


def gate_satisfied(gate: ApprovalGate, *, approved: bool, confirmed: bool) -> bool:
    """Whether a gate's requirement has actually been met.

    Checked server-side at execution time, not merely when the UI enabled a button
    (SPEC-003 §5). A client that forgets the confirmation step must fail here.
    """
    if gate == "AUTO":
        return True
    if gate == "EXPLICIT_APPROVAL":
        return approved
    return approved and confirmed


#: Labels and blurbs for the tier badge. In the domain rather than the UI because they
#: also appear in audit metadata — the reviewer and the record should use the same words.
RISK_META: dict[str, dict[str, str]] = {
    "LOW": {"label": "Low risk", "blurb": "Reversible and private to you."},
    "MEDIUM": {"label": "Medium risk", "blurb": "Visible to colleagues; awkward to undo."},
    "HIGH": {"label": "High risk", "blurb": "Reaches outside the org or destroys data."},
}
