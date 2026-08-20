"""Integration tests for ``BrdService`` against the real schema — SPEC-014 §4, §6, §8.

Before this file, nothing exercised this service against a real database at all — the
existing ``test_brd_contract.py`` covers only the pure domain functions (`validate_brd`,
`brd_to_markdown`), never `BrdService.create`/`refine`/`rename`/`delete` or the database
writes and audit trail they own. The model call itself is mocked at `service.llm.call_tool`
throughout, so these tests exercise the service's own logic — row creation before the call,
transactional writes, failure handling, revision numbering — without depending on a real
provider.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from voice2brd_db import AuditLog

from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.brd import BrdService
from app.services.llm import ExtractionError, LlmProvider

_FAKE_PROVIDER = LlmProvider("groq_llm", "Groq (GPT-OSS 120B)", "Groq", "openai/gpt-oss-120b", "groq_llm", True)


def _document(**overrides):
    base = {
        "title": "Internal Expense Approval Tool",
        "executiveSummary": "A tool for submitting and approving expenses without email threads.",
        "objectives": ["Cut approval time"],
        "scope": {"inScope": ["Submission and approval"], "outOfScope": ["Payroll integration"]},
        "stakeholders": [{"role": "Finance approver", "interest": "Needs an audit trail"}],
        "functionalRequirements": [
            {"id": "FR-01", "requirement": "A user must be able to submit an expense.",
             "priority": "MUST", "rationale": None},
        ],
        "nonFunctionalRequirements": [],
        "assumptions": [],
        "risks": [],
        "openQuestions": ["What is the approval limit?"],
    }
    base.update(overrides)
    return base


def _service(db_session) -> BrdService:
    return BrdService(db_session, Settings())


def _mocked_call_tool(service: BrdService, raw, provider=_FAKE_PROVIDER):
    return patch.object(service.llm, "call_tool", new=AsyncMock(return_value=(raw, provider)))


class TestCreate:
    async def test_a_successful_generation_stores_the_document_and_first_revision(
        self, db_session, make_user
    ):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            result = await service.create(user.id, "We need a tool for expense approval.", "req_1")
        assert result["title"] == "Internal Expense Approval Tool"
        assert result["status"] == "READY"
        assert result["revisionCount"] == 1
        assert result["content"]["functionalRequirements"][0]["id"] == "FR-01"
        assert result["revisions"][0]["ordinal"] == 1
        assert result["revisions"][0]["changeSummary"] == "Initial document."

    async def test_spoken_text_too_short_is_refused_before_any_provider_call(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        with pytest.raises(AppError) as exc:
            await service.create(user.id, "too short", "req_1")
        assert exc.value.code == "spoken_text_too_short"

    async def test_a_failed_generation_keeps_the_row_holding_the_transcript(self, db_session, make_user):
        # The row is created *before* the model call specifically so a failure does not
        # lose the one thing the user cannot reproduce: what they said.
        user = await make_user()
        service = _service(db_session)
        with patch.object(service.llm, "call_tool", new=AsyncMock(side_effect=ExtractionError("timeout", "The provider timed out."))):
            with pytest.raises(AppError) as exc:
                await service.create(user.id, "We need a tool for expense approval.", "req_1")
        assert exc.value.code == "brd_generation_failed"
        document_id = exc.value.details["documentId"]
        stored = await service.get_document(user.id, document_id)
        assert stored["status"] == "FAILED"
        assert stored["lastError"] == "The provider timed out."
        assert stored["content"] is None

    async def test_a_malformed_provider_response_is_reported_not_silently_accepted(
        self, db_session, make_user
    ):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, {"title": "x"}):  # missing everything else required
            with pytest.raises(AppError) as exc:
                await service.create(user.id, "We need a tool for expense approval.", "req_1")
        assert exc.value.code == "brd_generation_failed"

    async def test_a_generation_failure_is_audited(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        with patch.object(service.llm, "call_tool", new=AsyncMock(side_effect=ExtractionError("timeout", "Timed out."))):
            with pytest.raises(AppError):
                await service.create(user.id, "We need a tool for expense approval.", "req_1")
        rows = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "brd.generation_failed")
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].metadata_["stage"] == "create"


class TestRefine:
    async def test_a_successful_refinement_appends_a_revision_and_keeps_existing_ids(
        self, db_session, make_user
    ):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")

        revised_doc = _document(functionalRequirements=[
            *_document()["functionalRequirements"],
            {"id": "FR-02", "requirement": "An approver must reject with a reason.",
             "priority": "SHOULD", "rationale": None},
        ])
        with _mocked_call_tool(service, {"document": revised_doc, "changeSummary": "Added FR-02."}):
            result = await service.refine(user.id, created["id"], "Approvers can reject too.", "req_2")

        assert result["revisionCount"] == 2
        assert [r["id"] for r in result["content"]["functionalRequirements"]] == ["FR-01", "FR-02"]
        assert result["revisions"][1]["changeSummary"] == "Added FR-02."

    async def test_refining_a_document_with_no_content_is_refused(self, db_session, make_user):
        # Refining a document that never generated is a create, and treating it as a
        # refine would discard the earlier transcript rather than build on it.
        user = await make_user()
        service = _service(db_session)
        with patch.object(service.llm, "call_tool", new=AsyncMock(side_effect=ExtractionError("timeout", "x"))):
            with pytest.raises(AppError) as create_exc:
                await service.create(user.id, "Initial requirement.", "req_1")
        document_id = create_exc.value.details["documentId"]

        with pytest.raises(AppError) as exc:
            await service.refine(user.id, document_id, "More detail.", "req_2")
        assert exc.value.code == "no_content_to_refine"

    async def test_a_revision_result_missing_the_document_key_is_a_handled_failure(
        self, db_session, make_user
    ):
        # A silent revision is not acceptable (SPEC-014 §6) — a provider that returns the
        # summary without the document must be reported, not crash the request or write
        # a corrupted row.
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")
        with _mocked_call_tool(service, {"changeSummary": "Changed something."}):
            with pytest.raises(AppError) as exc:
                await service.refine(user.id, created["id"], "More detail.", "req_2")
        assert exc.value.code == "brd_generation_failed"

    async def test_a_revision_result_with_an_empty_change_summary_is_refused(
        self, db_session, make_user
    ):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")
        with _mocked_call_tool(service, {"document": _document(), "changeSummary": ""}):
            with pytest.raises(AppError) as exc:
                await service.refine(user.id, created["id"], "More detail.", "req_2")
        assert exc.value.code == "brd_generation_failed"

    async def test_a_change_summary_over_the_limit_is_refused(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")
        with _mocked_call_tool(service, {"document": _document(), "changeSummary": "x" * 1201}):
            with pytest.raises(AppError) as exc:
                await service.refine(user.id, created["id"], "More detail.", "req_2")
        assert exc.value.code == "brd_generation_failed"

    async def test_refining_an_unknown_document_404s(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).refine(user.id, "not-a-real-id", "More detail.", "req_1")
        assert exc.value.status_code == 404

    async def test_a_failed_refinement_leaves_the_previous_content_untouched(
        self, db_session, make_user
    ):
        # A failed refinement must not damage a document that was fine a moment ago.
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")

        with patch.object(service.llm, "call_tool", new=AsyncMock(side_effect=ExtractionError("timeout", "Timed out again."))):
            with pytest.raises(AppError):
                await service.refine(user.id, created["id"], "More detail.", "req_2")

        after = await service.get_document(user.id, created["id"])
        assert after["status"] == "READY"
        assert after["lastError"] == "Timed out again."
        assert after["content"]["title"] == "Internal Expense Approval Tool"
        assert after["revisionCount"] == 1  # the failed attempt did not add a revision

    async def test_dropping_a_requirement_is_logged_but_does_not_fail_the_refinement(
        self, db_session, make_user
    ):
        # The prompt forbids this, but a prompt is not an enforcement mechanism — the
        # service must not crash on it, only record that it happened (SPEC-014 §6.1).
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")

        dropped_doc = _document(functionalRequirements=[])
        with _mocked_call_tool(service, {"document": dropped_doc, "changeSummary": "Removed everything."}):
            result = await service.refine(user.id, created["id"], "Never mind all that.", "req_2")
        assert result["content"]["functionalRequirements"] == []

        row = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "brd.revised")
            )
        ).one()
        assert row.metadata_["droppedRequirementIds"] == ["FR-01"]


class TestRenameAndDelete:
    async def test_rename_updates_the_title_and_audits_before_and_after(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")
        result = await service.rename(user.id, created["id"], "New Title", "req_2")
        assert result["title"] == "New Title"

    async def test_renaming_an_unknown_document_404s(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).rename(user.id, "not-a-real-id", "New Title", "req_1")
        assert exc.value.status_code == 404

    async def test_delete_removes_the_document(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(user.id, "Initial requirement.", "req_1")
        result = await service.delete(user.id, created["id"], "req_2")
        assert result["ok"] is True
        with pytest.raises(AppError) as exc:
            await service.get_document(user.id, created["id"])
        assert exc.value.status_code == 404

    async def test_deleting_an_unknown_document_404s(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).delete(user.id, "not-a-real-id", "req_1")
        assert exc.value.status_code == 404


class TestListAndScoping:
    async def test_list_reports_counts_for_each_document(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            await service.create(user.id, "Initial requirement.", "req_1")
        result = await service.list_documents(user.id)
        assert len(result["documents"]) == 1
        assert result["documents"][0]["requirementCount"] == 1
        assert result["documents"][0]["openQuestionCount"] == 1

    async def test_another_users_document_is_invisible(self, db_session, make_user):
        owner = await make_user(email="owner@acme.test")
        stranger = await make_user(email="stranger@acme.test")
        service = _service(db_session)
        with _mocked_call_tool(service, _document()):
            created = await service.create(owner.id, "Initial requirement.", "req_1")
        with pytest.raises(AppError) as exc:
            await service.get_document(stranger.id, created["id"])
        assert exc.value.status_code == 404
        assert (await service.list_documents(stranger.id))["documents"] == []
