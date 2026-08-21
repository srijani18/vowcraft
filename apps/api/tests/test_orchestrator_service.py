"""Integration tests for ``OrchestratorService`` against the real schema — SPEC-002 §8.

``dependsOnId``/its ``blocks`` backref is the only populated ordering mechanism in this
schema — ``parentId``/``stepOrder`` are unused columns nothing creates or reads — so every
case here is built on a chain of items linked that way, exactly as ``prisma/seed.mjs``'s own
demo "dependency chain" already does.

Every id used below is captured into a plain string immediately after a row is created,
never read off the original object again afterward: ``run_workflow`` deliberately calls
``session.expire_all()`` between steps (see its comment for why), which leaves every
``ActionItem`` object touched here — including ones a test still holds a reference to —
expired. A plain string is immune to that; re-reading `.id` off an expired object outside
an active `await` is what actually crashes, with a `MissingGreenlet` error, not the
assertions this file cares about.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select
from vowcraft_db import ActionItem

from app.core.exceptions import AppError
from app.integrations.adapters import adapter_for as real_adapter_for
from app.integrations.types import ProviderError
from app.services.orchestrator import OrchestratorService

CALENDAR_PAYLOAD = {
    "title": "Q3 budget review",
    "startsAt": "2026-09-01T04:30:00.000Z",
    "durationMinutes": 45,
    "attendees": ["priya@acme.test"],
}
TASK_PAYLOAD = {"title": "Follow up with vendor"}


class _FailingAdapter:
    """Fails deterministically and non-retryably, so a test needs exactly one attempt."""

    def validate(self, payload):
        return []

    def preview(self, payload, *, time_zone, user_email):
        return {}

    async def execute(self, payload, ctx):
        raise ProviderError("boom", "The task API is down.", retryable=False)


def _adapter_for_with_one_failure(failing_provider_id: str):
    """The real registry for every provider except one, which always fails — lets a test
    make a single step in a chain fail deterministically without touching the others."""

    def _resolve(provider_id: str):
        if provider_id == failing_provider_id:
            return _FailingAdapter()
        return real_adapter_for(provider_id)

    return _resolve


def _service(db_session, api_settings) -> OrchestratorService:
    return OrchestratorService(db_session, api_settings)


async def _reload(db_session, item_id: str) -> ActionItem:
    return await db_session.scalar(select(ActionItem).where(ActionItem.id == item_id))


class TestSingleItem:
    async def test_an_item_with_no_dependents_runs_as_a_one_step_workflow(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        user_id, user_email = user.id, user.email
        transcript = await make_transcript(user)
        a_id = (await make_row(
            transcript, action_type="CALENDAR", status="APPROVED", payload=CALENDAR_PAYLOAD,
        )).id

        result = await _service(db_session, api_settings).run_workflow(user_id, user_email, a_id)
        assert result["ok"] is True
        assert [s["id"] for s in result["steps"]] == [a_id]
        assert result["haltedAt"] is None
        assert result["skippedIds"] == []


class TestLinearChain:
    async def test_a_linear_chain_executes_in_dependency_order(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        user_id, user_email = user.id, user.email
        transcript = await make_transcript(user)
        a_id = (await make_row(
            transcript, description="A", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD,
        )).id
        b_id = (await make_row(
            transcript, description="B", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD, depends_on_id=a_id,
        )).id
        c_id = (await make_row(
            transcript, description="C", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD, depends_on_id=b_id,
        )).id

        result = await _service(db_session, api_settings).run_workflow(user_id, user_email, a_id)
        assert result["ok"] is True
        assert [s["id"] for s in result["steps"]] == [a_id, b_id, c_id]
        assert all(s["status"] == "EXECUTED" for s in result["steps"])

    async def test_a_downstream_item_not_yet_approved_halts_the_chain_there(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        user_id, user_email = user.id, user.email
        transcript = await make_transcript(user)
        a_id = (await make_row(
            transcript, action_type="CALENDAR", status="APPROVED", payload=CALENDAR_PAYLOAD,
        )).id
        # Left at the default PROPOSED — never approved.
        b_id = (await make_row(
            transcript, action_type="CALENDAR", payload=CALENDAR_PAYLOAD, depends_on_id=a_id,
        )).id

        result = await _service(db_session, api_settings).run_workflow(user_id, user_email, a_id)
        assert [s["id"] for s in result["steps"]] == [a_id]
        assert result["haltedAt"]["id"] == b_id
        assert result["haltedAt"]["code"] == "not_approved"
        assert result["haltedAt"]["statusCode"] == 409

        # The key regression pin: B is untouched, not marked SKIPPED or anything else —
        # unattempted-and-unapproved is already an accurate, individually-gated state.
        reloaded = await _reload(db_session, b_id)
        assert reloaded.status == "PROPOSED"
        assert reloaded.execution_result is None


class TestFailureHalts:
    async def test_a_dispatch_failure_halts_the_chain_and_reports_the_error(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        user_id, user_email = user.id, user.email
        transcript = await make_transcript(user)
        a_id = (await make_row(
            transcript, action_type="CALENDAR", status="APPROVED", payload=CALENDAR_PAYLOAD,
        )).id
        b_id = (await make_row(
            transcript, action_type="TASK", status="APPROVED", payload=TASK_PAYLOAD,
            depends_on_id=a_id,
        )).id

        with patch(
            "app.services.executor.adapter_for",
            side_effect=_adapter_for_with_one_failure("notion"),
        ):
            result = await _service(db_session, api_settings).run_workflow(user_id, user_email, a_id)

        assert [s["id"] for s in result["steps"]] == [a_id]
        assert result["ok"] is False
        assert result["haltedAt"]["id"] == b_id
        assert result["haltedAt"]["statusCode"] == 502
        assert result["haltedAt"]["code"] == "execution_failed"


class TestBranching:
    async def test_a_branching_chain_halts_entirely_on_the_first_branch_failure(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        user_id, user_email = user.id, user.email
        transcript = await make_transcript(user)
        a_id = (await make_row(
            transcript, description="A", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD,
        )).id
        # Both B and C depend on A; C is independent of B and would otherwise be runnable.
        b_id = (await make_row(
            transcript, description="B", action_type="TASK", status="APPROVED",
            payload=TASK_PAYLOAD, depends_on_id=a_id,
        )).id
        c_id = (await make_row(
            transcript, description="C", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD, depends_on_id=a_id,
        )).id

        with patch(
            "app.services.executor.adapter_for",
            side_effect=_adapter_for_with_one_failure("notion"),
        ):
            result = await _service(db_session, api_settings).run_workflow(user_id, user_email, a_id)

        assert [s["id"] for s in result["steps"]] == [a_id]
        assert result["haltedAt"]["id"] == b_id
        # C was never attempted, even though nothing it itself depends on failed —
        # a whole-run halt, not a per-branch prune. See run_workflow's docstring.
        assert c_id in result["skippedIds"]
        reloaded = await _reload(db_session, c_id)
        assert reloaded.status == "APPROVED"
        assert reloaded.execution_result is None


class TestNotFound:
    async def test_an_unknown_item_id_is_not_found(self, make_user, db_session, api_settings):
        user = await make_user()
        try:
            await _service(db_session, api_settings).run_workflow(user.id, user.email, "does-not-exist")
            raise AssertionError("expected a not_found AppError")
        except AppError as exc:
            assert exc.status_code == 404

    async def test_another_users_item_is_not_found(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        owner = await make_user(email="owner@acme.test")
        stranger = await make_user(email="stranger@acme.test")
        transcript = await make_transcript(owner)
        a_id = (await make_row(
            transcript, action_type="CALENDAR", status="APPROVED", payload=CALENDAR_PAYLOAD,
        )).id
        try:
            await _service(db_session, api_settings).run_workflow(stranger.id, stranger.email, a_id)
            raise AssertionError("expected a not_found AppError")
        except AppError as exc:
            assert exc.status_code == 404


class TestPreview:
    async def test_preview_lists_the_chain_without_executing_anything(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        user_id = user.id
        transcript = await make_transcript(user)
        a_id = (await make_row(
            transcript, description="A", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD,
        )).id
        b_id = (await make_row(
            transcript, description="B", action_type="CALENDAR", status="APPROVED",
            payload=CALENDAR_PAYLOAD, depends_on_id=a_id,
        )).id

        result = await _service(db_session, api_settings).preview_workflow(user_id, a_id)
        assert [item["id"] for item in result["items"]] == [a_id, b_id]

        # preview_workflow never executes anything, so nothing here is expired — a plain
        # attribute check on either id is fine either way.
        reloaded_a = await _reload(db_session, a_id)
        reloaded_b = await _reload(db_session, b_id)
        assert reloaded_a.status == "APPROVED"
        assert reloaded_b.status == "APPROVED"
        assert reloaded_a.execution_result is None
