"""Integration tests for ``AuditService`` against the real schema — SPEC-003 §7.

Before this file, nothing exercised this service against a real database at all. The audit
that preceded this cutover found the FastAPI port used `OFFSET`-based pagination with no
`nextCursor` at all, while the live frontend (`AuditLogViewer.tsx`) is built entirely
around cursor-based "load more" — plus `actorLabel` and `actionItemDescription`, both
rendered directly by that component, were missing from the response, and free-text search
matched `requestId` instead of the linked action item's description. Every test below
exists because that comparison found a real divergence at that exact spot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vowcraft_db import AuditLog

from app.services.audit import AuditService

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def _service(db_session) -> AuditService:
    return AuditService(db_session)


async def _write(db_session, *, event, at, actor_id=None, actor_type="USER", action_item_id=None, request_id=None):
    row = AuditLog(
        event=event, at=at, actor_type=actor_type, actor_id=actor_id,
        action_item_id=action_item_id, request_id=request_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


class TestScoping:
    async def test_a_row_the_user_authored_is_visible(self, db_session, make_user):
        user = await make_user()
        await _write(db_session, event="profile.updated", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["total"] == 1

    async def test_a_system_row_tied_to_the_users_own_action_item_is_visible(
        self, db_session, make_user, make_transcript, make_row
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        item = await make_row(transcript)
        await _write(db_session, event="action_item.executed", at=BASE, actor_type="SYSTEM", action_item_id=item.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["total"] == 1

    async def test_another_users_row_is_invisible(self, db_session, make_user):
        owner = await make_user(email="owner@acme.test")
        stranger = await make_user(email="stranger@acme.test")
        await _write(db_session, event="profile.updated", at=BASE, actor_id=owner.id)
        result = await _service(db_session).list_entries(stranger.id)
        assert result["total"] == 0


class TestActorLabel:
    async def test_system_and_agent_are_labelled_generically(self, db_session, make_user):
        user = await make_user()
        await _write(db_session, event="a.b", at=BASE, actor_id=user.id, actor_type="SYSTEM")
        await _write(db_session, event="a.c", at=BASE - timedelta(seconds=1), actor_id=user.id, actor_type="AGENT")
        result = await _service(db_session).list_entries(user.id)
        labels = {e["event"]: e["actorLabel"] for e in result["entries"]}
        assert labels["a.b"] == "System"
        assert labels["a.c"] == "Agent"

    async def test_a_named_actor_shows_their_name(self, db_session, make_user):
        user = await make_user(name="Priya Raman")
        await _write(db_session, event="profile.updated", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["entries"][0]["actorLabel"] == "Priya Raman"

    async def test_an_unnamed_actor_falls_back_to_email(self, db_session, make_user):
        user = await make_user(name=None, email="noname@acme.test")
        await _write(db_session, event="profile.updated", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["entries"][0]["actorLabel"] == "noname@acme.test"

    async def test_a_deleted_actor_is_labelled_honestly(self, db_session, make_user, make_transcript, make_row):
        # actorId is SET NULL on user deletion — a row can legitimately have no actor
        # left to name, and must not be attributed to whoever happens to own the query.
        user = await make_user()
        transcript = await make_transcript(user)
        item = await make_row(transcript)
        await _write(db_session, event="action_item.approved", at=BASE, actor_id=None, action_item_id=item.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["entries"][0]["actorLabel"] == "Deleted account"


class TestActionItemDescription:
    async def test_the_related_action_items_description_is_included(
        self, db_session, make_user, make_transcript, make_row
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        item = await make_row(transcript, description="Send the Q3 budget to finance")
        await _write(db_session, event="action_item.approved", at=BASE, actor_id=user.id, action_item_id=item.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["entries"][0]["actionItemDescription"] == "Send the Q3 budget to finance"

    async def test_a_row_with_no_action_item_has_no_description(self, db_session, make_user):
        user = await make_user()
        await _write(db_session, event="profile.updated", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id)
        assert result["entries"][0]["actionItemDescription"] is None


class TestSearch:
    async def test_search_matches_the_action_items_description_not_the_request_id(
        self, db_session, make_user, make_transcript, make_row
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        item = await make_row(transcript, description="Renew the vendor contract")
        await _write(
            db_session, event="action_item.approved", at=BASE, actor_id=user.id,
            action_item_id=item.id, request_id="req_opaque_id_123",
        )
        by_description = await _service(db_session).list_entries(user.id, search="vendor contract")
        assert by_description["total"] == 1
        by_request_id = await _service(db_session).list_entries(user.id, search="req_opaque_id_123")
        assert by_request_id["total"] == 0

    async def test_search_also_matches_the_event_name(self, db_session, make_user):
        user = await make_user()
        await _write(db_session, event="profile.password_changed", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id, search="password")
        assert result["total"] == 1


class TestFilters:
    async def test_event_filters_to_the_named_types(self, db_session, make_user):
        user = await make_user()
        await _write(db_session, event="profile.updated", at=BASE, actor_id=user.id)
        await _write(db_session, event="settings.updated", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id, events=["settings.updated"])
        assert result["total"] == 1
        assert result["entries"][0]["event"] == "settings.updated"

    async def test_action_item_id_filters_to_that_items_trail(
        self, db_session, make_user, make_transcript, make_row
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        item_a = await make_row(transcript, description="A")
        item_b = await make_row(transcript, description="B")
        await _write(db_session, event="action_item.approved", at=BASE, actor_id=user.id, action_item_id=item_a.id)
        await _write(db_session, event="action_item.approved", at=BASE, actor_id=user.id, action_item_id=item_b.id)
        result = await _service(db_session).list_entries(user.id, action_item_id=item_a.id)
        assert result["total"] == 1
        assert result["entries"][0]["actionItemId"] == item_a.id

    async def test_since_excludes_earlier_rows(self, db_session, make_user):
        user = await make_user()
        await _write(db_session, event="a.old", at=BASE - timedelta(days=2), actor_id=user.id)
        await _write(db_session, event="a.new", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id, since=BASE - timedelta(days=1))
        assert result["total"] == 1
        assert result["entries"][0]["event"] == "a.new"


class TestFacets:
    async def test_facets_are_unaffected_by_the_active_filter(self, db_session, make_user):
        # So the filter chips still show what else exists, not only what is selected.
        user = await make_user()
        await _write(db_session, event="profile.updated", at=BASE, actor_id=user.id)
        await _write(db_session, event="settings.updated", at=BASE, actor_id=user.id)
        result = await _service(db_session).list_entries(user.id, events=["settings.updated"])
        facet_events = {f["event"] for f in result["facets"]["events"]}
        assert facet_events == {"profile.updated", "settings.updated"}


class TestPagination:
    async def test_more_rows_than_the_limit_yields_a_next_cursor(self, db_session, make_user):
        user = await make_user()
        for i in range(3):
            await _write(db_session, event=f"e.{i}", at=BASE - timedelta(seconds=i), actor_id=user.id)
        result = await _service(db_session).list_entries(user.id, limit=2)
        assert len(result["entries"]) == 2
        assert result["nextCursor"] is not None
        assert result["total"] == 3

    async def test_the_cursor_continues_where_the_previous_page_stopped_with_no_gap_or_overlap(
        self, db_session, make_user
    ):
        user = await make_user()
        for i in range(5):
            await _write(db_session, event=f"e.{i}", at=BASE - timedelta(seconds=i), actor_id=user.id)
        first = await _service(db_session).list_entries(user.id, limit=2)
        second = await _service(db_session).list_entries(user.id, limit=2, cursor=first["nextCursor"])
        third = await _service(db_session).list_entries(user.id, limit=2, cursor=second["nextCursor"])
        seen = [e["event"] for e in first["entries"] + second["entries"] + third["entries"]]
        assert seen == ["e.0", "e.1", "e.2", "e.3", "e.4"]
        assert third["nextCursor"] is None

    async def test_rows_sharing_the_same_timestamp_are_still_paginated_without_a_duplicate_or_a_gap(
        self, db_session, make_user
    ):
        # A single transaction routinely writes more than one audit row in the same
        # millisecond — `id desc` is what keeps the page boundary well-defined when
        # `at` alone cannot.
        user = await make_user()
        for i in range(4):
            await _write(db_session, event=f"tie.{i}", at=BASE, actor_id=user.id)
        first = await _service(db_session).list_entries(user.id, limit=2)
        second = await _service(db_session).list_entries(user.id, limit=2, cursor=first["nextCursor"])
        first_ids = {e["id"] for e in first["entries"]}
        second_ids = {e["id"] for e in second["entries"]}
        assert first_ids.isdisjoint(second_ids)
        assert len(first_ids | second_ids) == 4
