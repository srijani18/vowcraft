"""Integration tests for ``ProfileService`` against the real schema — SPEC-005 §2, §3, §6, §8.

Before this file, nothing exercised this service against a real database at all. The audit
that preceded this cutover found the deletion-confirmation check (the account's own email,
typed out) was entirely absent from the FastAPI port — any authenticated `DELETE` scheduled
deletion regardless of body — plus a missing `minPasswordLength` field and no way to restart
the onboarding tour, both of which the live frontend actively calls. Every test below exists
because that comparison found a real gap at that exact spot.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from voice2brd_db import AuditLog

from app.core.exceptions import AppError
from app.services.settings_profile import ProfileService


def _service(db_session) -> ProfileService:
    return ProfileService(db_session)


class TestGet:
    async def test_reports_min_password_length_for_the_form_to_enforce(self, db_session, make_user):
        user = await make_user()
        view = await _service(db_session).get(user)
        assert view["minPasswordLength"] == 12

    async def test_a_fresh_account_has_no_password(self, db_session, make_user):
        user = await make_user()
        view = await _service(db_session).get(user)
        assert view["hasPassword"] is False


class TestRename:
    async def test_a_valid_rename_persists_and_is_audited(self, db_session, make_user):
        user = await make_user(name="Before")
        result = await _service(db_session).rename(user, "After", "req_1")
        assert result["name"] == "After"
        rows = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "profile.updated")
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].before == {"name": "Before"}
        assert rows[0].after == {"name": "After"}

    async def test_an_empty_name_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).rename(user, "   ", "req_1")
        assert exc.value.status_code == 422
        assert exc.value.code == "name_required"

    async def test_resending_the_current_name_writes_no_audit_row(self, db_session, make_user):
        user = await make_user(name="Same")
        await _service(db_session).rename(user, "Same", "req_1")
        rows = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "profile.updated")
            )
        ).all()
        assert len(rows) == 0


class TestOnboarding:
    async def test_complete_sets_a_terminal_state(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).complete_onboarding(
            user, action="complete", reached_step=5, request_id="req_1"
        )
        assert result["onboardingCompletedAt"] is not None
        assert result["onboardingSkipped"] is False

    async def test_skip_is_equally_terminal(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).complete_onboarding(
            user, action="skip", reached_step=None, request_id="req_1"
        )
        assert result["onboardingCompletedAt"] is not None
        assert result["onboardingSkipped"] is True

    async def test_restart_clears_it_so_the_tour_can_run_again(self, db_session, make_user):
        # The live "Show the tour again" button in ProfileManager.tsx sends exactly this —
        # a capability entirely absent from the FastAPI port before this fix.
        user = await make_user()
        await _service(db_session).complete_onboarding(user, action="complete", reached_step=3, request_id="req_1")
        result = await _service(db_session).complete_onboarding(
            user, action="restart", reached_step=None, request_id="req_2"
        )
        assert result["onboardingCompletedAt"] is None
        assert result["onboardingSkipped"] is False

    async def test_the_reached_step_is_recorded_for_tour_analytics(self, db_session, make_user):
        user = await make_user()
        await _service(db_session).complete_onboarding(
            user, action="skip", reached_step=7, request_id="req_1"
        )
        row = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "profile.onboarding")
            )
        ).one()
        assert row.metadata_ == {"action": "skip", "reachedStep": 7}


class TestDeletion:
    async def test_a_mismatched_confirmation_changes_nothing(self, db_session, make_user):
        user = await make_user(email="reviewer@acme.test")
        with pytest.raises(AppError) as exc:
            await _service(db_session).request_deletion(user, "wrong@example.com", "req_1")
        assert exc.value.status_code == 422
        assert exc.value.code == "confirmation_mismatch"
        assert user.deletion_requested_at is None

    async def test_the_confirmation_is_case_insensitive(self, db_session, make_user):
        user = await make_user(email="Reviewer@Acme.Test")
        result = await _service(db_session).request_deletion(user, "reviewer@acme.test", "req_1")
        assert result["deletionRequestedAt"] is not None

    async def test_a_correct_confirmation_schedules_deletion_with_a_grace_period(self, db_session, make_user):
        user = await make_user(email="reviewer@acme.test")
        result = await _service(db_session).request_deletion(user, "reviewer@acme.test", "req_1")
        assert result["deletionRequestedAt"] is not None
        assert result["deletionEffectiveAt"] is not None

    async def test_requesting_deletion_twice_is_refused(self, db_session, make_user):
        user = await make_user(email="reviewer@acme.test")
        await _service(db_session).request_deletion(user, "reviewer@acme.test", "req_1")
        with pytest.raises(AppError) as exc:
            await _service(db_session).request_deletion(user, "reviewer@acme.test", "req_2")
        assert exc.value.status_code == 409
        assert exc.value.code == "deletion_already_requested"

    async def test_deletion_can_be_cancelled_inside_the_window(self, db_session, make_user):
        user = await make_user(email="reviewer@acme.test")
        await _service(db_session).request_deletion(user, "reviewer@acme.test", "req_1")
        result = await _service(db_session).cancel_deletion(user, "req_2")
        assert result["deletionRequestedAt"] is None

    async def test_cancelling_with_nothing_pending_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).cancel_deletion(user, "req_1")
        assert exc.value.status_code == 409
        assert exc.value.code == "no_deletion_pending"
