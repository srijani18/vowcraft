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


REGISTRY: dict[str, Provider] = {
    "google_calendar": Provider(
        "google_calendar", "Google Calendar", "CALENDAR", "oauth", None,
        "https://console.cloud.google.com/apis/credentials",
    ),
    "notion": Provider(
        "notion", "Notion", "TASK", "oauth", None, "https://www.notion.so/my-integrations"
    ),
    "gmail": Provider(
        "gmail", "Gmail", "EMAIL", "oauth", None,
        "https://console.cloud.google.com/apis/credentials",
    ),
    "sendgrid": Provider(
        "sendgrid", "SendGrid", "EMAIL", "api_key", "sendgrid", "https://app.sendgrid.com/settings/api_keys"
    ),
    "slack": Provider(
        "slack", "Slack", "REMINDER", "oauth", None, "https://api.slack.com/apps"
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
        # misconfiguration; fall through to the default rather than mis-executing.
        if provider and provider.capability == action_type:
            return provider
    fallback = DEFAULT_BY_CAPABILITY.get(action_type)
    return REGISTRY.get(fallback) if fallback else None


def providers_for(action_type: str) -> list[Provider]:
    """Every provider that can serve a capability, default first."""
    default = DEFAULT_BY_CAPABILITY.get(action_type)
    matching = [p for p in REGISTRY.values() if p.capability == action_type]
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
