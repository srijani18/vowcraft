"""Integration tests for ``ActionItemService`` against the real schema — SPEC-001.

Everything here runs against Postgres (see ``db_session`` in conftest.py), not a mock
session: the bugs this rewrite was fixing (missing facets/pagination, a stale readiness
check, multiple audit rows per PATCH) only show up against real constraints and enum
types, not against a hand-rolled fake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from vowcraft_db import ActionItem, AuditLog, Correction

from app.core.exceptions import AppError
from app.db.repositories.users import AuditRepository
from app.services.action_items import ActionItemService

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _service(db_session, api_settings) -> ActionItemService:
    return ActionItemService(db_session, api_settings)


class TestDependsOn:
    async def test_get_item_reports_what_it_depends_on(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        # A regression pin: to_dto() hardcoded `"dependsOn": None` unconditionally, so the
        # dependency gate (SPEC-002 §8) was enforced server-side at execute time but never
        # actually visible in the API response — a reviewer had no way to see *what* an
        # item was blocked on.
        user = await make_user()
        transcript = await make_transcript(user)
        blocker = await make_row(transcript, description="Finalize the vendor contract", status="APPROVED")
        blocked = await make_row(
            transcript, description="Send the signed copy", depends_on_id=blocker.id
        )

        result = await _service(db_session, api_settings).get_item(user.id, user.email, blocked.id)

        assert result["dependsOn"] == {
            "id": blocker.id,
            "description": "Finalize the vendor contract",
            "status": "APPROVED",
        }


class TestListItemsFacets:
    async def test_owners_and_transcripts_are_reported(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user, title="Q3 planning")
        await make_row(transcript, owner_name="Priya Raman")
        await make_row(transcript, owner_name="Alex Rivera")
        await make_row(transcript, owner_name=None)

        result = await _service(db_session, api_settings).list_items(user.id, user.email)

        assert result["facets"]["owners"] == ["Alex Rivera", "Priya Raman"]
        assert result["facets"]["transcripts"] == [{"id": transcript.id, "title": "Q3 planning"}]

    async def test_facets_are_scoped_to_the_requesting_user(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        me = await make_user(email="me@acme.test")
        someone_else = await make_user(email="other@acme.test")
        my_transcript = await make_transcript(me)
        their_transcript = await make_transcript(someone_else)
        await make_row(my_transcript, owner_name="Priya Raman")
        await make_row(their_transcript, owner_name="Should Not Appear")

        result = await _service(db_session, api_settings).list_items(me.id, me.email)

        assert result["facets"]["owners"] == ["Priya Raman"]


class TestListItemsFilters:
    async def test_readiness_filter(self, make_user, make_transcript, make_row, db_session, api_settings):
        user = await make_user()
        transcript = await make_transcript(user)
        ready = await make_row(transcript, description="Ready item", owner_name="Priya Raman")
        await make_row(transcript, description="Needs clarification", owner_name=None)

        result = await _service(db_session, api_settings).list_items(
            user.id, user.email, readiness=["READY"]
        )

        ids = [item["id"] for item in result["items"]]
        assert ids == [ready.id]

    async def test_deadline_bucket_filter(self, make_user, make_transcript, make_row, db_session, api_settings):
        user = await make_user()
        transcript = await make_transcript(user)
        overdue = await make_row(
            transcript,
            description="Overdue",
            # Naive, matching how the column is actually stored (TIMESTAMP WITHOUT TIME
            # ZONE) — asyncpg rejects a tz-aware value outright for that column type.
            deadline=(datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None),
        )
        await make_row(transcript, description="No deadline", deadline=None)

        result = await _service(db_session, api_settings).list_items(
            user.id, user.email, deadline="overdue"
        )

        ids = [item["id"] for item in result["items"]]
        assert ids == [overdue.id]

    async def test_owner_filter_is_case_insensitive_and_exact(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        match = await make_row(transcript, owner_name="Priya Raman")
        await make_row(transcript, owner_name="Priya Ramanujan")  # must not match a substring

        result = await _service(db_session, api_settings).list_items(
            user.id, user.email, owner="priya raman"
        )

        assert [item["id"] for item in result["items"]] == [match.id]

    async def test_search_matches_description_or_source_quote(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        by_description = await make_row(transcript, description="Send the invoice to finance")
        by_quote = await make_row(
            transcript, description="Follow up", source_quote="we agreed on the invoice terms"
        )
        await make_row(transcript, description="Unrelated item")

        result = await _service(db_session, api_settings).list_items(
            user.id, user.email, search="invoice"
        )

        ids = {item["id"] for item in result["items"]}
        assert ids == {by_description.id, by_quote.id}


class TestListItemsPagination:
    async def test_cursor_pagination_visits_every_item_exactly_once_in_sort_order(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        # Distinct source timestamps so review_sort_key gives a strict, known order once
        # priority and deadline are tied.
        created = [
            await make_row(transcript, description=f"Item {i}", source_timestamp_ms=i * 1000)
            for i in range(5)
        ]

        service = _service(db_session, api_settings)
        seen: list[str] = []
        cursor = None
        for _ in range(10):  # generous upper bound so a broken loop fails instead of hanging
            page = await service.list_items(user.id, user.email, limit=2, cursor=cursor)
            seen.extend(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break

        assert seen == [row.id for row in created]

    async def test_counts_reflect_the_current_page_not_the_full_result_set(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        # A deliberately-kept quirk carried over from the TypeScript implementation:
        # the header badges count what is loaded, not the total. Pinning it so a future
        # "fix" is a conscious decision, not an accidental behaviour change.
        user = await make_user()
        transcript = await make_transcript(user)
        for i in range(3):
            await make_row(transcript, description=f"Item {i}")

        result = await _service(db_session, api_settings).list_items(user.id, user.email, limit=2)

        assert result["counts"]["total"] == 2
        assert result["nextCursor"] is not None


class TestPatchItem:
    async def test_editing_several_fields_writes_one_correction_per_field_and_one_audit_row(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, description="Original", owner_name="Priya Raman", priority="LOW")

        await _service(db_session, api_settings).patch_item(
            user.id, user.email, row.id,
            fields={"description": "Updated", "owner_name": "Alex Rivera", "priority": "HIGH"},
            request_id="req-1",
        )

        corrections = (
            await db_session.scalars(select(Correction).where(Correction.action_item_id == row.id))
        ).all()
        assert {c.field for c in corrections} == {"description", "owner_name", "priority"}

        audit_rows = (
            await db_session.scalars(select(AuditLog).where(AuditLog.action_item_id == row.id))
        ).all()
        assert len(audit_rows) == 1, "one PATCH must produce exactly one audit row (SPEC-003 §10)"
        assert audit_rows[0].before == {"description": "Original", "owner_name": "Priya Raman", "priority": "LOW"}
        assert audit_rows[0].after == {"description": "Updated", "owner_name": "Alex Rivera", "priority": "HIGH"}

    async def test_confidence_change_is_audited_but_is_not_a_correction(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, confidence="MEDIUM")

        await _service(db_session, api_settings).patch_item(
            user.id, user.email, row.id, confidence="HIGH", request_id="req-1",
        )

        corrections = (
            await db_session.scalars(select(Correction).where(Correction.action_item_id == row.id))
        ).all()
        assert corrections == []
        audit_rows = (
            await db_session.scalars(select(AuditLog).where(AuditLog.action_item_id == row.id))
        ).all()
        assert len(audit_rows) == 1

    async def test_a_noop_patch_writes_nothing(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, description="Unchanged", priority="MEDIUM")

        await _service(db_session, api_settings).patch_item(
            user.id, user.email, row.id,
            fields={"description": "Unchanged", "priority": "MEDIUM"},
            request_id="req-1",
        )

        audit_rows = (
            await db_session.scalars(select(AuditLog).where(AuditLog.action_item_id == row.id))
        ).all()
        assert audit_rows == []

    async def test_clearing_a_field_to_null_is_detected_as_a_change(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, owner_name="Priya Raman")

        result = await _service(db_session, api_settings).patch_item(
            user.id, user.email, row.id, fields={"owner_name": None}, request_id="req-1",
        )

        assert result["ownerName"] is None
        corrections = (
            await db_session.scalars(select(Correction).where(Correction.action_item_id == row.id))
        ).all()
        assert [c.field for c in corrections] == ["owner_name"]

    async def test_a_deadline_instant_that_is_unchanged_is_not_a_correction_even_with_a_different_offset(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        # The database stores a naive-but-UTC deadline; the client sends a tz-aware ISO
        # string. Comparing them naively (or via mismatched JSON text) would flag "no
        # actual change" as a change on every single save.
        user = await make_user()
        transcript = await make_transcript(user)
        stored = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        row = await make_row(transcript, deadline=stored.replace(tzinfo=None))

        # Same instant, expressed with an explicit (non-UTC) offset.
        same_instant_other_offset = datetime(2026, 9, 1, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))

        await _service(db_session, api_settings).patch_item(
            user.id, user.email, row.id, fields={"deadline": same_instant_other_offset}, request_id="req-1",
        )

        corrections = (
            await db_session.scalars(select(Correction).where(Correction.action_item_id == row.id))
        ).all()
        assert corrections == []

    async def test_decision_note_lands_as_audit_metadata(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user, )
        row = await make_row(transcript, status="PROPOSED")

        await _service(db_session, api_settings).patch_item(
            user.id, user.email, row.id, status="APPROVED", decision_note="Looks right to me.",
            request_id="req-1",
        )

        audit_rows = (
            await db_session.scalars(select(AuditLog).where(AuditLog.action_item_id == row.id))
        ).all()
        assert audit_rows[0].metadata_ == {"note": "Looks right to me."}
        assert audit_rows[0].event == "action_item.approved"

    async def test_an_illegal_status_transition_is_rejected(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        from app.core.exceptions import AppError

        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, status="EXECUTED")

        try:
            await _service(db_session, api_settings).patch_item(
                user.id, user.email, row.id, status="PROPOSED", request_id="req-1",
            )
            raise AssertionError("expected an AppError for an illegal transition")
        except AppError as exc:
            assert exc.code == "illegal_transition"

    async def test_a_server_only_status_is_rejected(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        from app.core.exceptions import AppError

        user = await make_user()
        transcript = await make_transcript(user)
        # APPROVED -> EXECUTING is a structurally valid transition (the executor's own
        # step 9 makes exactly this move) — the point of this fixture is that validity
        # alone is not enough; EXECUTING is still something only the executor may assign.
        row = await make_row(transcript, status="APPROVED")

        try:
            await _service(db_session, api_settings).patch_item(
                user.id, user.email, row.id, status="EXECUTING", request_id="req-1",
            )
            raise AssertionError("expected an AppError for a client-claimed server-only status")
        except AppError as exc:
            assert exc.code == "server_only_status"

    async def test_a_transition_illegal_regardless_of_shape_is_reported_as_illegal_not_server_only(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        # A regression pin: checking `SERVER_ONLY_STATUSES` before `transition_error()`
        # made *every* PATCH naming EXECUTED report `server_only_status`, even from a
        # status (PROPOSED) that could never legally reach EXECUTED at all — a
        # transition-shape question misreported as a permissions one.
        from app.core.exceptions import AppError

        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, status="PROPOSED")

        try:
            await _service(db_session, api_settings).patch_item(
                user.id, user.email, row.id, status="EXECUTED", request_id="req-1",
            )
            raise AssertionError("expected an AppError for an illegal transition")
        except AppError as exc:
            assert exc.code == "illegal_transition"


class TestBulkUpdate:
    async def test_approve_maps_the_op_to_a_status_and_reports_per_item_results(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        a = await make_row(transcript, status="PROPOSED")
        b = await make_row(transcript, status="PROPOSED")

        result = await _service(db_session, api_settings).bulk_update(
            user.id, user.email, [a.id, b.id], "approve", "req-1",
        )

        assert result["okCount"] == 2
        assert result["failedCount"] == 0
        assert {r["id"] for r in result["results"]} == {a.id, b.id}
        assert all(r["ok"] for r in result["results"])

    async def test_a_partial_failure_still_reports_the_items_that_succeeded(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        approvable = await make_row(transcript, status="PROPOSED")
        already_executed = await make_row(transcript, status="EXECUTED")

        result = await _service(db_session, api_settings).bulk_update(
            user.id, user.email, [approvable.id, already_executed.id], "approve", "req-1",
        )

        assert result["okCount"] == 1
        assert result["failedCount"] == 1
        by_id = {r["id"]: r for r in result["results"]}
        assert by_id[approvable.id]["ok"] is True
        assert by_id[already_executed.id]["ok"] is False
        assert "error" in by_id[already_executed.id]

    async def test_note_is_forwarded_to_every_item(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        row = await make_row(transcript, status="PROPOSED")

        await _service(db_session, api_settings).bulk_update(
            user.id, user.email, [row.id], "reject", "req-1", note="Duplicate of another item.",
        )

        audit_rows = (
            await db_session.scalars(select(AuditLog).where(AuditLog.action_item_id == row.id))
        ).all()
        assert audit_rows[0].metadata_ == {"note": "Duplicate of another item."}


class TestDeleteItem:
    """SPEC-001. Deleting is board housekeeping, not a decision about the action — the
    audit trail is what makes that distinction safe, and `AuditLog.actionItemId` is
    SET NULL so the trail outlives the row (it was CASCADE until this feature existed,
    which would have made one click erase the record of what was executed)."""

    async def test_an_item_is_deleted(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        item_id = (await make_row(transcript, status="PROPOSED")).id

        result = await ActionItemService(db_session, api_settings).delete_item(
            user.id, item_id, "r1"
        )

        assert result == {"ok": True}
        assert await db_session.scalar(
            select(func.count()).select_from(ActionItem).where(ActionItem.id == item_id)
        ) == 0

    async def test_the_audit_trail_survives(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        """The property the schema change exists for. The rows detach rather than vanish."""
        user = await make_user()
        transcript = await make_transcript(user)
        item_id = (await make_row(transcript, status="EXECUTED")).id
        await AuditRepository(db_session).record(
            event="action_item.executed", actor_id=user.id, action_item_id=item_id
        )
        await db_session.flush()

        await ActionItemService(db_session, api_settings).delete_item(user.id, item_id, "r1")
        await db_session.flush()

        surviving = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event == "action_item.executed")
        )
        assert surviving == 1, "the audit row was deleted with the item"

    async def test_an_executed_item_can_be_deleted(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        """Not refused: what it did in the world already happened, the audit keeps the
        record, and refusing would leave the board permanently un-tidyable."""
        user = await make_user()
        transcript = await make_transcript(user)
        item_id = (await make_row(transcript, status="EXECUTED")).id

        assert await ActionItemService(db_session, api_settings).delete_item(
            user.id, item_id, "r1"
        ) == {"ok": True}

    async def test_an_executing_item_is_refused(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        """A dispatch is in flight against a third party; deleting the row would leave its
        result with nowhere to be recorded."""
        user = await make_user()
        transcript = await make_transcript(user)
        item_id = (await make_row(transcript, status="EXECUTING")).id

        with pytest.raises(AppError) as excinfo:
            await ActionItemService(db_session, api_settings).delete_item(user.id, item_id, "r1")

        assert excinfo.value.code == "execution_in_progress"

    async def test_another_users_item_is_not_found(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        """404 rather than 403, so a guessed id is not an enumeration oracle."""
        mine = await make_user(email="mine@acme.test")
        theirs = await make_user(email="theirs@acme.test")
        their_transcript = await make_transcript(theirs)
        their_item = (await make_row(their_transcript, status="PROPOSED")).id

        with pytest.raises(AppError) as excinfo:
            await ActionItemService(db_session, api_settings).delete_item(
                mine.id, their_item, "r1"
            )

        assert excinfo.value.status_code == 404
        assert await db_session.scalar(
            select(func.count()).select_from(ActionItem).where(ActionItem.id == their_item)
        ) == 1, "another user's item was deleted"

    async def test_a_blocking_item_can_be_deleted_without_taking_dependents(
        self, make_user, make_transcript, make_row, db_session, api_settings
    ):
        """`dependsOnId` is SET NULL, so the chain loosens instead of cascading into a
        multi-item deletion nobody asked for."""
        user = await make_user()
        transcript = await make_transcript(user)
        blocker_id = (await make_row(transcript, status="PROPOSED")).id
        dependent_id = (
            await make_row(transcript, status="PROPOSED", depends_on_id=blocker_id)
        ).id

        await ActionItemService(db_session, api_settings).delete_item(user.id, blocker_id, "r1")
        await db_session.flush()

        assert await db_session.scalar(
            select(func.count()).select_from(ActionItem).where(ActionItem.id == dependent_id)
        ) == 1, "the dependent was deleted too"
