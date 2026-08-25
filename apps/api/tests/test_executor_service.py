"""Integration tests for ``ExecutorService`` against the real schema — SPEC-002 §5.

This pins a real bug: the frontend reads `preview.preview.fields` and top-level
`riskTier`/`riskFactors`/`warnings`/`approvalGate` off the execute response
(`src/components/action-items/ExecuteModal.tsx`), and `result.summary` off a completed
one (`Board.tsx`'s success toast). Before this file, `ExecutorService.execute()` had zero
test coverage of any kind, and its response was missing every one of those fields —
`preview.preview.fields.map(...)` would have thrown the moment a real user opened the
execute confirmation modal. Every assertion below exists because it would have caught
that.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import AppError
from app.services.executor import ExecutorService

CALENDAR_PAYLOAD = {
    "title": "Q3 budget review",
    "startsAt": "2026-09-01T04:30:00.000Z",
    "durationMinutes": 45,
    "attendees": ["priya@acme.test", "jordan@acme.test"],
}


def _service(db_session, api_settings) -> ExecutorService:
    return ExecutorService(db_session, api_settings)


class TestDryRunPreview:
    async def test_the_preview_the_modal_actually_reads_is_present(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(
            transcript,
            action_type="CALENDAR",
            status="APPROVED",
            owner_name="Priya Raman",
            payload=CALENDAR_PAYLOAD,
        )

        outcome = await _service(db_session, api_settings).execute(
            user.id, user.email, row.id, dry_run=True, request_id="req-1",
        )

        # Top-level — ExecuteModal.tsx reads these directly off the outcome, not nested.
        assert "riskTier" in outcome
        assert "riskFactors" in outcome
        assert "approvalGate" in outcome
        assert "warnings" in outcome

        # Nested — the field-by-field confirmation table. `.fields` is what would have
        # thrown: `preview.preview.fields.map(...)` on a response that never had it.
        preview = outcome["preview"]
        assert preview["provider"] == "google_calendar"
        assert isinstance(preview["consequence"], str) and preview["consequence"]
        assert isinstance(preview["fields"], list) and len(preview["fields"]) > 0
        for field in preview["fields"]:
            assert set(field.keys()) == {"label", "value"}

        assert outcome["result"] is None
        assert outcome["dryRun"] is True

    async def test_every_adapter_produces_a_reachable_preview(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        # One case per provider, so a signature mismatch in any single adapter's preview()
        # (a missing kwarg, a KeyError on a payload field) is caught here rather than only
        # for whichever provider a manual click-through happens to exercise.
        cases = [
            ("CALENDAR", CALENDAR_PAYLOAD, "google_calendar"),
            ("TASK", {"title": "Follow up with vendor"}, "notion"),
            (
                "EMAIL",
                {"to": ["a@acme.test"], "subject": "Hi", "body": "Body text", "sendMode": "draft"},
                "gmail",
            ),
            # REMINDER is deliberately absent: Slack is its only provider and Slack is
            # gated, so there is nothing to preview. Covered by
            # `test_a_gated_capability_is_refused_rather_than_previewed` below — previewing
            # an execution the product will not perform would be a tease.
        ]
        user = await make_user()
        transcript = await make_transcript(user)
        service = _service(db_session, api_settings)
        for action_type, payload, expected_provider in cases:
            row = await make_row(
                transcript, action_type=action_type, status="APPROVED",
                owner_name="Priya Raman", payload=payload,
            )
            outcome = await service.execute(user.id, user.email, row.id, dry_run=True, request_id="req-1")
            assert outcome["preview"]["provider"] == expected_provider, action_type
            assert outcome["preview"]["fields"], action_type

    async def test_a_gated_capability_is_refused_rather_than_previewed(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        """Slack is built but withheld, and it is REMINDER's only provider — so a reminder
        cannot execute. The refusal names the reason as a product state (`provider_gated`)
        rather than a misconfiguration: there is nothing here for the reviewer to fix, and
        "no integration is configured" would send them to a settings page to fix it."""
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(
            transcript, action_type="REMINDER", status="APPROVED",
            owner_name="Priya Raman",
            payload={"message": "Ping the team", "channel": "self",
                     "remindAt": "2026-09-01T04:30:00.000Z"},
        )

        with pytest.raises(AppError) as excinfo:
            await _service(db_session, api_settings).execute(
                user.id, user.email, row.id, dry_run=True, request_id="req-1"
            )

        assert excinfo.value.code == "provider_gated"
        assert "coming soon" in excinfo.value.message.lower()


class TestSuccessfulExecution:
    async def test_a_mock_execution_reports_a_summary_and_the_top_level_fields(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(
            transcript, action_type="CALENDAR", status="APPROVED",
            owner_name="Priya Raman", payload=CALENDAR_PAYLOAD,
        )

        outcome = await _service(db_session, api_settings).execute(
            user.id, user.email, row.id, request_id="req-1",
        )

        assert outcome["ok"] is True
        assert outcome["status"] == "EXECUTED"
        for key in ("riskTier", "riskFactors", "approvalGate", "warnings"):
            assert key in outcome
        # This is what Board.tsx's success toast actually shows as `detail`.
        assert isinstance(outcome["result"]["summary"], str) and outcome["result"]["summary"]
        assert outcome["result"]["simulated"] is True


class TestReplay:
    async def test_replaying_an_identical_request_reports_a_real_risk_assessment_and_a_summary(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(
            transcript, action_type="CALENDAR", status="APPROVED",
            owner_name="Priya Raman", payload=CALENDAR_PAYLOAD,
        )
        service = _service(db_session, api_settings)

        first = await service.execute(user.id, user.email, row.id, request_id="req-1")
        second = await service.execute(user.id, user.email, row.id, request_id="req-2")

        assert first["result"]["externalId"] == second["result"]["externalId"]
        assert second["replayed"] is True
        # Not a placeholder tier: replaying a real assessment must still say what it is.
        assert second["riskTier"] in ("LOW", "MEDIUM", "HIGH")
        assert second["approvalGate"] == "AUTO"
        assert second["warnings"] == []
        assert isinstance(second["result"]["summary"], str) and second["result"]["summary"]
