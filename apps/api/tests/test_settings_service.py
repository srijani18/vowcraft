"""Integration tests for ``SettingsService`` against the real schema — SPEC-005 §4.1.

Before this file, nothing exercised this service against a real database at all. The audit
that preceded this cutover found that every hand-written business rule in
``src/server/settings/service.ts`` — workday ordering, time zone existence, org-domain
format, provider-routing existence — had been dropped in the FastAPI port, silently: the
route accepted values the original would have refused with a specific `422`. Every test
below exists because that comparison found a real gap at that exact spot, not because the
surface area invites one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from vowcraft_db import AuditLog

from app.api.routes.settings import SettingsPatch
from app.core.exceptions import AppError
from app.services.settings_profile import SettingsService


def _service(db_session) -> SettingsService:
    return SettingsService(db_session)


class TestGetDefaults:
    async def test_a_user_with_no_settings_row_gets_the_documented_defaults(self, db_session, make_user):
        user = await make_user()
        view = await _service(db_session).get(user.id)
        assert view["timeZone"] == "Asia/Kolkata"
        assert view["workdayStart"] == "09:00"
        assert view["workdayEnd"] == "18:00"
        assert view["orgDomains"] == []
        assert view["approvalThresholds"] == {}

    async def test_routing_options_offer_only_capabilities_with_more_than_one_provider(self, db_session, make_user):
        # CALENDAR (google_calendar), TASK (notion) and REMINDER (slack) each have exactly
        # one adapter — offering a "choice" of one is not a choice. Only EMAIL (gmail,
        # sendgrid) has two.
        user = await make_user()
        view = await _service(db_session).get(user.id)
        capabilities = {o["capability"] for o in view["routingOptions"]}
        assert capabilities == {"EMAIL"}
        email = next(o for o in view["routingOptions"] if o["capability"] == "EMAIL")
        assert {p["id"] for p in email["providers"]} == {"gmail", "sendgrid"}
        assert next(p for p in email["providers"] if p["id"] == "gmail")["isDefault"] is True
        assert next(p for p in email["providers"] if p["id"] == "sendgrid")["isDefault"] is False

    async def test_time_zones_are_offered_for_the_picker(self, db_session, make_user):
        user = await make_user()
        view = await _service(db_session).get(user.id)
        assert "Asia/Kolkata" in view["timeZones"]
        assert "UTC" in view["timeZones"]


class TestUpdateHappyPath:
    async def test_a_valid_patch_persists_and_is_returned(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).update(
            user.id, {"timeZone": "Europe/London", "allowWeekends": True}, "req_1"
        )
        assert result["timeZone"] == "Europe/London"
        assert result["allowWeekends"] is True
        # Re-fetching confirms it was actually written, not just echoed back.
        refetched = await _service(db_session).get(user.id)
        assert refetched["timeZone"] == "Europe/London"

    async def test_an_account_with_no_settings_row_gets_one_created_on_first_patch(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).update(user.id, {"allowWeekends": True}, "req_1")
        assert result["allowWeekends"] is True
        assert result["timeZone"] == "Asia/Kolkata"  # untouched fields still carry defaults

    async def test_only_actually_changed_fields_are_audited(self, db_session, make_user):
        user = await make_user()
        await _service(db_session).update(user.id, {"timeZone": "Europe/London", "allowWeekends": False}, "req_1")
        rows = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "settings.updated")
            )
        ).all()
        assert len(rows) == 1
        # `allowWeekends: False` is the default — resending it is not a change, and must
        # not appear in the diff alongside the field that actually moved.
        assert set(rows[0].after.keys()) == {"timeZone"}

    async def test_resending_the_current_value_writes_no_audit_row(self, db_session, make_user):
        # A deliberate improvement over the TypeScript original, which audited every PATCH
        # regardless of whether anything changed — see the note in settings_profile.py.
        user = await make_user()
        await _service(db_session).update(user.id, {"timeZone": "Asia/Kolkata"}, "req_1")
        rows = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "settings.updated")
            )
        ).all()
        assert len(rows) == 0


class TestGuardrailValidation:
    """Each of these protects `RuleContext.settings`, not just the form (SPEC-005 §4.1) —
    a malformed value here silently disables a guardrail rather than merely looking wrong."""

    async def test_a_malformed_clock_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).update(user.id, {"workdayStart": "25:00"}, "req_1")
        assert exc.value.status_code == 422
        assert exc.value.code == "invalid_clock"

    async def test_a_single_digit_hour_is_accepted_like_the_original(self, db_session, make_user):
        # `parseClock` in the TS original accepts "9:00", not only "09:00" — a Pydantic
        # `pattern=` constraint that required zero-padding would reject a value the
        # original considered valid.
        user = await make_user()
        result = await _service(db_session).update(user.id, {"workdayStart": "9:00"}, "req_1")
        assert result["workdayStart"] == "9:00"

    async def test_an_unknown_time_zone_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).update(user.id, {"timeZone": "Mars/Olympus"}, "req_1")
        assert exc.value.status_code == 422
        assert exc.value.code == "invalid_time_zone"

    async def test_an_inverted_working_day_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).update(
                user.id, {"workdayStart": "18:00", "workdayEnd": "09:00"}, "req_1"
            )
        assert exc.value.status_code == 422
        assert exc.value.code == "invalid_workday"

    async def test_the_workday_ordering_check_uses_the_stored_value_when_only_one_side_changes(
        self, db_session, make_user
    ):
        # Setting only `workdayStart` past the *stored* `workdayEnd` (18:00 by default)
        # must still be caught — the check cannot only compare two values from the same
        # request.
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).update(user.id, {"workdayStart": "19:00"}, "req_1")
        assert exc.value.code == "invalid_workday"

    async def test_a_malformed_org_domain_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).update(user.id, {"orgDomains": ["not a domain"]}, "req_1")
        assert exc.value.status_code == 422
        assert exc.value.code == "invalid_domain"

    async def test_a_valid_domain_is_lowercased_and_stripped_of_its_leading_at(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).update(user.id, {"orgDomains": ["@ACME.Test"]}, "req_1")
        assert result["orgDomains"] == ["acme.test"]

    async def test_routing_email_to_a_task_only_provider_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).update(user.id, {"providerRouting": {"EMAIL": "notion"}}, "req_1")
        assert exc.value.status_code == 422
        assert exc.value.code == "invalid_routing"

    async def test_routing_email_to_sendgrid_is_accepted(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).update(user.id, {"providerRouting": {"EMAIL": "sendgrid"}}, "req_1")
        assert result["providerRouting"]["EMAIL"] == "sendgrid"


class TestSettingsPatchSchema:
    """Pure model tests — no database needed. `budgetApprovalLimit`'s upper bound and
    `approvalThresholds`/`providerRouting`'s key enum are genuinely schema-level in the
    original (`z.coerce.number()...max(100_000_000)`, `z.record(z.enum(ACTION_TYPES), ...)`),
    so they belong here as Pydantic constraints rather than in `SettingsService.update` — a
    rejection either raises produces the same `400 invalid_request` shape FastAPI's
    `RequestValidationError` handler already gives, matching zod's own `ZodError` handling."""

    def test_org_currency_is_uppercased(self):
        assert SettingsPatch(orgCurrency="usd").orgCurrency == "USD"

    def test_an_unknown_action_type_key_is_rejected(self):
        with pytest.raises(ValidationError):
            SettingsPatch(approvalThresholds={"SLACK_MESSAGE": "AUTO"})

    def test_an_unknown_gate_value_is_rejected(self):
        with pytest.raises(ValidationError):
            SettingsPatch(approvalThresholds={"EMAIL": "JUST_DO_IT"})

    def test_a_budget_limit_over_the_cap_is_rejected(self):
        with pytest.raises(ValidationError):
            SettingsPatch(budgetApprovalLimit=100_000_001)

    def test_a_well_formed_patch_validates(self):
        patch = SettingsPatch(
            timeZone="UTC", approvalThresholds={"EMAIL": "AUTO"}, providerRouting={"EMAIL": "gmail"}
        )
        assert patch.approvalThresholds == {"EMAIL": "AUTO"}
