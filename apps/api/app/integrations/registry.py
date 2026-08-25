"""Provider registry — a port of ``src/integrations/registry.ts``.

Routing only: which provider serves which action type, and whether it is configured. The
adapters that actually call Google, Notion, Gmail and Slack are separate modules behind one
interface, so ``server`` and ``domain`` never import a vendor SDK.

``resolve`` returns ``None`` rather than guessing. An unroutable action must surface as a
``422 no_provider``, never as a silent no-op — a request that appears to succeed and reaches
nobody is the worst outcome available here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import Settings


@dataclass(frozen=True)
class Provider:
    id: str
    display_name: str
    #: The single action type it can serve. One capability per provider keeps routing a
    #: lookup rather than a negotiation.
    capability: str
    auth: str  # "oauth" | "api_key" | "none"
    credential_service: Optional[str]
    docs_url: str
    #: "live" | "planned". A planned provider is built but deliberately not offered yet, so
    #: `resolve_provider` refuses to route to it. Refusing is the point: routing to a gated
    #: provider would execute against a third party the product says is unavailable, and
    #: silently falling back to a *different* provider would send mail from somewhere the
    #: reviewer never approved.
    status: str = "live"


REGISTRY: dict[str, Provider] = {
    "google_calendar": Provider(
        "google_calendar", "Google Calendar", "CALENDAR", "oauth", None,
        "https://console.cloud.google.com/apis/credentials",
    ),
    # `credential_service` is set despite this being an OAuth provider: the vault row is
    # not where its *auth* comes from (that's the OAuth token), but it does hold the target
    # task database id, which the adapter needs and extraction cannot supply.
    "notion": Provider(
        "notion", "Notion", "TASK", "oauth", "notion", "https://www.notion.so/my-integrations"
    ),
    "gmail": Provider(
        "gmail", "Gmail", "EMAIL", "oauth", None,
        "https://console.cloud.google.com/apis/credentials",
    ),
    # Gated: implemented and tested, but not part of what the product currently offers.
    # SendGrid is safe to gate — Gmail serves EMAIL — and it cannot save drafts anyway,
    # which is the reversible path this system prefers.
    "sendgrid": Provider(
        "sendgrid", "SendGrid", "EMAIL", "api_key", "sendgrid",
        "https://app.sendgrid.com/settings/api_keys", status="planned",
    ),
    # Gated, with a consequence worth stating: Slack is the *only* REMINDER provider, so
    # while it is planned, a REMINDER action has nowhere to execute and fails
    # `422 no_provider`. Extraction still records reminders — they are real commitments and
    # dropping them would lose information — they simply cannot be executed yet, which the
    # board shows rather than hides.
    "slack": Provider(
        "slack", "Slack", "REMINDER", "oauth", None, "https://api.slack.com/apps",
        status="planned",
    ),
}

#: The provider used when the user has expressed no preference.
DEFAULT_BY_CAPABILITY: dict[str, str] = {
    "CALENDAR": "google_calendar",
    "TASK": "notion",
    "EMAIL": "gmail",
    "REMINDER": "slack",
}


def get_provider(provider_id: str) -> Optional[Provider]:
    return REGISTRY.get(provider_id)


def resolve_provider(action_type: str, routing: Optional[dict[str, str]] = None) -> Optional[Provider]:
    """Route an action type to a provider, honouring a per-user override."""
    override = (routing or {}).get(action_type)
    if override:
        provider = get_provider(override)
        # An override naming a provider that cannot serve this action type is a
        # misconfiguration; fall through to the default rather than mis-executing. A
        # *gated* override falls through too — a stored routing preference must not
        # resurrect a provider the product has withdrawn.
        if provider and provider.capability == action_type and provider.status == "live":
            return provider
    fallback = DEFAULT_BY_CAPABILITY.get(action_type)
    candidate = REGISTRY.get(fallback) if fallback else None
    # None rather than the next provider with this capability: falling through would send
    # via somewhere the reviewer never approved. `422 no_provider` is the honest answer.
    return candidate if candidate and candidate.status == "live" else None


def planned_provider_for(action_type: str) -> Optional[Provider]:
    """The gated provider that *would* serve this capability, if any.

    Exists so "you cannot execute this" can say why. "No integration is configured" is the
    right message for a capability nobody has set up, and the wrong one for a capability
    that is deliberately withheld — it sends the reader to a settings page to fix something
    that is not theirs to fix.
    """
    return next(
        (
            p
            for p in REGISTRY.values()
            if p.capability == action_type and p.status == "planned"
        ),
        None,
    )


def providers_for(action_type: str) -> list[Provider]:
    """Every provider that can serve a capability, default first."""
    default = DEFAULT_BY_CAPABILITY.get(action_type)
    # Gated providers are excluded, so the settings picker cannot offer a route that
    # `resolve_provider` will then refuse.
    matching = [
        p for p in REGISTRY.values() if p.capability == action_type and p.status == "live"
    ]
    return sorted(matching, key=lambda p: 0 if p.id == default else 1)


ACTION_TYPES: tuple[str, ...] = ("CALENDAR", "TASK", "EMAIL", "REMINDER")


def routable_capabilities() -> list[tuple[str, list[Provider]]]:
    """Capabilities with more than one adapter — the ones worth offering a choice for.

    A port of ``src/integrations/registry.ts``'s ``routableCapabilities`` — used by the
    settings surface to build its provider-routing picker (SPEC-005 §4.1).
    """
    return [(t, providers_for(t)) for t in ACTION_TYPES if len(providers_for(t)) > 1]


def provider_mode(provider_id: str, settings: Settings) -> str:
    """``live`` or ``mock``, per provider.

    Surfaced in the UI chrome rather than buried in settings: a reviewer must never be
    unsure whether a click reaches the real world (SPEC-002 §4).
    """
    return "live" if settings.integration_is_live(provider_id) else "mock"
