"""Settings and profile — SPEC-005.

Both are small and both write audit rows, so they share a module rather than each having a
file with one function in it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vowcraft_db import User, UserSettings, now_ms

from app.core.exceptions import conflict, not_found, unprocessable
from app.db.repositories.users import AuditRepository, UserRepository
from app.domain.password_rules import MIN_PASSWORD_LENGTH, validate_name
from app.domain.types import SettingsView
from app.integrations.registry import providers_for, routable_capabilities

#: How long a deletion request can be undone. Long enough to notice a mistake, short enough
#: that "deleted" means something (SPEC-005 §6).
DELETION_GRACE_DAYS = 7

_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DOMAIN_RE = re.compile(r"^@?[a-z0-9.-]+\.[a-z]{2,}$", re.IGNORECASE)

#: Mirrors src/server/settings/service.ts's ROUTING_NOTES — shown next to each choice in
#: the provider-routing picker.
_ROUTING_NOTES: dict[str, str] = {
    "gmail": "Sends as you. Can save a draft, so it is the reversible choice.",
    "sendgrid": "Sends as the organisation from a verified domain. Cannot save drafts.",
    "google_calendar": "Creates events on your primary calendar and invites attendees.",
    "notion": "Creates a page in your task database.",
    "slack": "Schedules a message via chat.scheduleMessage.",
}


def _parse_clock(value: str) -> Optional[int]:
    """Minutes since midnight, or None if `value` does not look like "9:00"/"09:00"."""
    match = _CLOCK_RE.match(value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _valid_time_zone(value: str) -> bool:
    try:
        ZoneInfo(value)
        return True
    except Exception:  # noqa: BLE001 — any of zoneinfo's several failure modes means "no"
        return False


def _ui_extras() -> dict[str, Any]:
    """`routingOptions` and `timeZones` — server config for the form's pickers, not stored
    state, so they are added at the API boundary rather than living in `settings_to_view`
    (which also backs the before/after audit diff, where they would just be dead weight)."""
    return {
        "routingOptions": [
            {
                "capability": capability,
                "providers": [
                    {
                        "id": p.id,
                        "displayName": p.display_name,
                        "isDefault": index == 0,
                        "note": _ROUTING_NOTES.get(p.id, ""),
                    }
                    for index, p in enumerate(providers)
                ],
            }
            for capability, providers in routable_capabilities()
        ],
        "timeZones": sorted(available_timezones()),
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def settings_to_view(row: Optional[UserSettings]) -> dict[str, Any]:
    defaults = SettingsView()
    if row is None:
        return {
            "timeZone": defaults.time_zone,
            "workdayStart": defaults.workday_start,
            "workdayEnd": defaults.workday_end,
            "allowWeekends": defaults.allow_weekends,
            "maxMeetingMinutes": defaults.max_meeting_minutes,
            "minBufferMinutes": defaults.min_buffer_minutes,
            "orgDomains": [],
            "autoExecuteLowRisk": defaults.auto_execute_low_risk,
            "budgetApprovalLimit": defaults.budget_approval_limit,
            "orgCurrency": defaults.org_currency,
            "approvalThresholds": {},
            "providerRouting": {},
            "updatedAt": None,
        }
    return {
        "timeZone": row.time_zone,
        "workdayStart": row.workday_start,
        "workdayEnd": row.workday_end,
        "allowWeekends": row.allow_weekends,
        "maxMeetingMinutes": row.max_meeting_minutes,
        "minBufferMinutes": row.min_buffer_minutes,
        "orgDomains": list(row.org_domains or []),
        "autoExecuteLowRisk": row.auto_execute_low_risk,
        "budgetApprovalLimit": row.budget_approval_limit,
        "orgCurrency": row.org_currency,
        "approvalThresholds": dict(row.approval_thresholds or {}),
        "providerRouting": dict(row.provider_routing or {}),
        "updatedAt": _iso(row.updated_at_),
    }


_FIELD_MAP = {
    "timeZone": "time_zone",
    "workdayStart": "workday_start",
    "workdayEnd": "workday_end",
    "allowWeekends": "allow_weekends",
    "maxMeetingMinutes": "max_meeting_minutes",
    "minBufferMinutes": "min_buffer_minutes",
    "orgDomains": "org_domains",
    "autoExecuteLowRisk": "auto_execute_low_risk",
    "budgetApprovalLimit": "budget_approval_limit",
    "orgCurrency": "org_currency",
    "approvalThresholds": "approval_thresholds",
    "providerRouting": "provider_routing",
}


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> dict[str, Any]:
        row = await self.session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
        return {**settings_to_view(row), **_ui_extras()}

    async def update(self, user_id: str, patch: dict[str, Any], request_id: str) -> dict[str, Any]:
        """A port of `updateSettings` in `src/server/settings/service.ts`.

        Every field here lands in `RuleContext.settings` (SPEC-005 §4.1), so validation is
        stricter than a normal settings form: a malformed value would not be cosmetic, it
        would silently disable a guardrail. The checks below are explicit rather than
        Pydantic field constraints precisely so each can answer its own `422` code — see
        the note on `SettingsPatch` in `apps/api/app/api/routes/settings.py`.
        """
        row = await self.session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
        if row is None:
            # An account created before settings existed, or by a path that skipped them.
            # Creating on demand beats failing a request the user cannot fix.
            row = UserSettings(user_id=user_id)
            self.session.add(row)
            await self.session.flush()

        if "timeZone" in patch and not _valid_time_zone(patch["timeZone"]):
            raise unprocessable(
                "invalid_time_zone", f"“{patch['timeZone']}” is not a time zone this server knows."
            )

        for key in ("workdayStart", "workdayEnd"):
            if key in patch and _parse_clock(patch[key]) is None:
                raise unprocessable("invalid_clock", f"{key} must look like 09:00.", {"field": key})

        start = _parse_clock(patch.get("workdayStart", row.workday_start))
        end = _parse_clock(patch.get("workdayEnd", row.workday_end))
        if start is not None and end is not None and end <= start:
            # An inverted window would make SCHED_HOURS block literally everything, which
            # looks like the app is broken rather than like a bad setting.
            raise unprocessable(
                "invalid_workday", "The end of the working day must be after the start.",
                {"field": "workdayEnd"},
            )

        if "orgDomains" in patch:
            bad = next((d for d in patch["orgDomains"] if not _DOMAIN_RE.match(d.strip())), None)
            if bad is not None:
                raise unprocessable("invalid_domain", f"“{bad}” is not a valid domain.", {"field": "orgDomains"})
            # A leading "@" is what people type; storing it would break the
            # internal-address comparison, which matches on the domain alone.
            patch["orgDomains"] = [d.strip().lower().lstrip("@") for d in patch["orgDomains"]]

        if "providerRouting" in patch:
            for capability, provider_id in patch["providerRouting"].items():
                allowed = {p.id for p in providers_for(capability)}
                if provider_id not in allowed:
                    raise unprocessable(
                        "invalid_routing", f"{provider_id} cannot execute {capability} actions.",
                        {"field": "providerRouting"},
                    )

        before = settings_to_view(row)
        for key, value in patch.items():
            attr = _FIELD_MAP.get(key)
            if attr is None or value is None:
                continue
            setattr(row, attr, value)
        await self.session.flush()
        # Refreshed, not just flushed. `updatedAt` is computed by the database
        # (`onupdate=func.now()`), so after a flush SQLAlchemy marks it expired and reads it
        # back on next access — which in an async session is an implicit lazy load and
        # raises MissingGreenlet. Refreshing fetches it in the async context instead.
        await self.session.refresh(row)

        after = settings_to_view(row)
        # Unlike the original, which always wrote an audit row for any PATCH (even one that
        # resent an unchanged value), this only writes one when something actually moved —
        # a deliberate improvement, not a gap: an auditor gains nothing from a no-op entry.
        changed = {k: after[k] for k in after if before.get(k) != after.get(k) and k != "updatedAt"}
        if changed:
            await AuditRepository(self.session).record(
                event="settings.updated", actor_id=user_id, request_id=request_id,
                before={k: before[k] for k in changed}, after=changed,
            )
        return {**after, **_ui_extras()}


class ProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.audit = AuditRepository(session)

    async def get(self, user: User) -> dict[str, Any]:
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "image": user.image,
            "hasPassword": bool(user.password_hash),
            "passwordUpdatedAt": _iso(user.password_updated_at),
            "onboardingCompletedAt": _iso(user.onboarding_completed_at),
            "onboardingSkipped": user.onboarding_skipped,
            "deletionRequestedAt": _iso(user.deletion_requested_at),
            # When it actually happens, so the UI can count down rather than saying
            # "scheduled" with no horizon.
            "deletionEffectiveAt": (
                _iso(user.deletion_requested_at + timedelta(days=DELETION_GRACE_DAYS))
                if user.deletion_requested_at
                else None
            ),
            "createdAt": _iso(user.created_at_),
            # So the password form can enforce/display the minimum without hardcoding it
            # on both sides.
            "minPasswordLength": MIN_PASSWORD_LENGTH,
        }

    async def rename(self, user: User, name: str, request_id: str) -> dict[str, Any]:
        problem = validate_name(name)
        if problem:
            raise unprocessable(problem.code, problem.message, {"field": problem.field})
        before = user.name
        after = name.strip()
        if after != before:
            user.name = after
            await self.session.flush()
            await self.audit.record(
                event="profile.updated", actor_id=user.id, request_id=request_id,
                before={"name": before}, after={"name": after},
            )
        return await self.get(user)

    async def complete_onboarding(
        self, user: User, *, action: str, reached_step: Optional[int], request_id: str
    ) -> dict[str, Any]:
        await self.users.touch_onboarding(user, action=action)
        await self.audit.record(
            event="profile.onboarding", actor_id=user.id, request_id=request_id,
            metadata={"action": action, "reachedStep": reached_step},
        )
        return await self.get(user)

    async def request_deletion(self, user: User, confirm: str, request_id: str) -> dict[str, Any]:
        """Schedules deletion rather than performing it.

        `confirm` must be the account's own email, typed out — a checkbox is too easy to
        click through for something irreversible. The row survives the grace period so an
        accidental delete is recoverable. Being scheduled does *not* freeze the account in
        the meantime — `optional_user` deliberately keeps authenticating it (see the note
        there), because refusing it would make the grace period decorative: the one thing
        this state must still allow is signing in to cancel (SPEC-005 §6, §8).
        """
        if confirm.strip().lower() != user.email.lower():
            raise unprocessable(
                "confirmation_mismatch", "That does not match your email address. Nothing has been deleted."
            )
        if user.deletion_requested_at is not None:
            raise conflict("deletion_already_requested", "Deletion is already scheduled for this account.")
        user.deletion_requested_at = now_ms()
        await self.session.flush()
        await self.audit.record(
            event="profile.deletion_requested", actor_id=user.id, request_id=request_id,
            metadata={"graceDays": DELETION_GRACE_DAYS},
        )
        return await self.get(user)

    async def cancel_deletion(self, user: User, request_id: str) -> dict[str, Any]:
        if user.deletion_requested_at is None:
            raise conflict("no_deletion_pending", "This account is not scheduled for deletion.")
        user.deletion_requested_at = None
        await self.session.flush()
        await self.audit.record(
            event="profile.deletion_cancelled", actor_id=user.id, request_id=request_id
        )
        return await self.get(user)
