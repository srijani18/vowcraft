"""Integration tests for ``DecisionService`` against the real schema — SPEC-020.

Decisions have been fully extracted and stored since SPEC-010, but never had a reading
surface of their own — only a per-transcript count. This service is the first thing that
reads the `Decision` table for anything other than feeding the `POL_CONTRADICTS_DECISION`
guardrail (`app/services/action_items.py`), so it gets the same scoping/pagination
scrutiny as the other cross-transcript feeds (audit log, action items).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.decisions import DecisionService

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def _service(db_session) -> DecisionService:
    return DecisionService(db_session)


class TestScoping:
    async def test_a_decision_on_the_callers_own_transcript_is_visible(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript)
        result = await _service(db_session).list_decisions(user.id)
        assert result["total"] == 1

    async def test_a_decision_on_another_users_transcript_is_invisible(
        self, db_session, make_user, make_transcript, make_decision
    ):
        owner = await make_user(email="owner@acme.test")
        stranger = await make_user(email="stranger@acme.test")
        transcript = await make_transcript(owner)
        await make_decision(transcript)
        result = await _service(db_session).list_decisions(stranger.id)
        assert result["total"] == 0


class TestFilters:
    async def test_transcript_id_restricts_to_that_transcripts_decisions_only(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        one = await make_transcript(user, title="Sprint planning")
        two = await make_transcript(user, title="Vendor renewal")
        await make_decision(one, statement="Ship in October")
        await make_decision(two, statement="Renew the vendor contract")
        result = await _service(db_session).list_decisions(user.id, transcript_id=one.id)
        assert result["total"] == 1
        assert result["decisions"][0]["statement"] == "Ship in October"

    async def test_decided_by_is_case_insensitive(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript, decided_by="Priya Raman")
        result = await _service(db_session).list_decisions(user.id, decided_by="priya raman")
        assert result["total"] == 1

    async def test_q_matches_the_statement(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript, statement="We will ship invoicing in October")
        result = await _service(db_session).list_decisions(user.id, search="invoicing")
        assert result["total"] == 1

    async def test_q_matches_the_source_quote(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript, source_quote="so let's commit to the October date")
        result = await _service(db_session).list_decisions(user.id, search="commit to the October date")
        assert result["total"] == 1

    async def test_q_does_not_match_an_unrelated_field(
        self, db_session, make_user, make_transcript, make_decision
    ):
        # Regression pin, same spirit as the audit log's "search matches description, not
        # requestId": a free-text filter must not accidentally match on an internal id.
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript)
        result = await _service(db_session).list_decisions(user.id, search=transcript.id)
        assert result["total"] == 0


class TestFacets:
    async def test_transcript_facet_lists_every_transcript_even_without_a_matching_decision(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        one = await make_transcript(user, title="Sprint planning")
        two = await make_transcript(user, title="Vendor renewal")
        await make_decision(one)
        result = await _service(db_session).list_decisions(user.id, transcript_id=one.id)
        titles = {t["title"] for t in result["facets"]["transcripts"]}
        assert titles == {"Sprint planning", "Vendor renewal"}
        assert result["total"] == 1  # the filter itself still applies to the page

    async def test_decided_by_facet_is_the_distinct_non_null_set(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript, decided_by="Priya Raman")
        await make_decision(transcript, decided_by="Priya Raman", statement="A second decision")
        await make_decision(transcript, decided_by=None, statement="An unattributed decision")
        result = await _service(db_session).list_decisions(user.id)
        assert result["facets"]["decidedBy"] == ["Priya Raman"]


class TestPagination:
    async def test_more_rows_than_the_limit_yields_a_next_cursor(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        for i in range(3):
            await make_decision(
                transcript, statement=f"Decision {i}", created_at_=BASE - timedelta(seconds=i)
            )
        result = await _service(db_session).list_decisions(user.id, limit=2)
        assert len(result["decisions"]) == 2
        assert result["nextCursor"] is not None
        assert result["total"] == 3

    async def test_the_cursor_continues_where_the_previous_page_stopped_with_no_gap_or_overlap(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        for i in range(5):
            await make_decision(
                transcript, statement=f"Decision {i}", created_at_=BASE - timedelta(seconds=i)
            )
        first = await _service(db_session).list_decisions(user.id, limit=2)
        second = await _service(db_session).list_decisions(user.id, limit=2, cursor=first["nextCursor"])
        third = await _service(db_session).list_decisions(user.id, limit=2, cursor=second["nextCursor"])
        seen = [d["statement"] for d in first["decisions"] + second["decisions"] + third["decisions"]]
        assert seen == ["Decision 0", "Decision 1", "Decision 2", "Decision 3", "Decision 4"]
        assert third["nextCursor"] is None

    async def test_rows_sharing_the_same_timestamp_are_still_paginated_without_a_duplicate_or_a_gap(
        self, db_session, make_user, make_transcript, make_decision
    ):
        # Every decision from one meeting is written in the same transaction, so they all
        # share a `createdAt` — `id desc` is what keeps the page boundary well-defined.
        user = await make_user()
        transcript = await make_transcript(user)
        for i in range(4):
            await make_decision(transcript, statement=f"Tie {i}", created_at_=BASE)
        first = await _service(db_session).list_decisions(user.id, limit=2)
        second = await _service(db_session).list_decisions(user.id, limit=2, cursor=first["nextCursor"])
        first_ids = {d["id"] for d in first["decisions"]}
        second_ids = {d["id"] for d in second["decisions"]}
        assert first_ids.isdisjoint(second_ids)
        assert len(first_ids | second_ids) == 4


class TestTimestampLabel:
    async def test_a_null_source_timestamp_yields_a_null_label(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript, source_timestamp_ms=None)
        result = await _service(db_session).list_decisions(user.id)
        assert result["decisions"][0]["sourceTimestampLabel"] is None

    async def test_a_timestamp_over_an_hour_uses_the_h_mm_ss_form(
        self, db_session, make_user, make_transcript, make_decision
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_decision(transcript, source_timestamp_ms=3_723_000)  # 1h 2m 3s
        result = await _service(db_session).list_decisions(user.id)
        assert result["decisions"][0]["sourceTimestampLabel"] == "1:02:03"
