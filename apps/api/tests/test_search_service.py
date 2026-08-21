"""Integration tests for ``SearchService`` against the real schema — SPEC-021.

Deliberately HTTP-mock-free: this tests only the storage/ranking layer, taking a
pre-computed query vector as input. Embedding the query text is `embeddings.py`'s job,
already covered by `test_embeddings_service.py`.
"""

from __future__ import annotations

from app.services.search import SearchService


def _vec(value: float) -> list[float]:
    return [value] * 512


def _service(db_session) -> SearchService:
    return SearchService(db_session)


class TestRanking:
    async def test_hits_are_ordered_nearest_first(
        self, make_user, make_transcript, make_segment, make_segment_embedding, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        far = await make_segment(transcript, text="far segment", start_ms=0)
        near = await make_segment(transcript, text="near segment", start_ms=5000)
        await make_segment_embedding(far, embedding=_vec(-1.0))
        await make_segment_embedding(near, embedding=_vec(1.0))

        result = await _service(db_session).search(user.id, _vec(1.0))
        ids = [r["segmentId"] for r in result["results"]]
        assert ids[0] == near.id
        assert ids[-1] == far.id


class TestScoping:
    async def test_another_users_segment_is_never_returned_even_if_objectively_closer(
        self, make_user, make_transcript, make_segment, make_segment_embedding, db_session
    ):
        owner = await make_user(email="owner@acme.test")
        stranger = await make_user(email="stranger@acme.test")
        owner_transcript = await make_transcript(owner)
        stranger_transcript = await make_transcript(stranger)
        owner_segment = await make_segment(owner_transcript, text="owner's segment")
        stranger_segment = await make_segment(stranger_transcript, text="stranger's segment")
        # The stranger's embedding is the objectively closer match to the query...
        await make_segment_embedding(stranger_segment, embedding=_vec(1.0))
        await make_segment_embedding(owner_segment, embedding=_vec(0.5))

        result = await _service(db_session).search(owner.id, _vec(1.0))
        ids = {r["segmentId"] for r in result["results"]}
        assert ids == {owner_segment.id}


class TestFilters:
    async def test_transcript_id_narrows_to_one_transcript(
        self, make_user, make_transcript, make_segment, make_segment_embedding, db_session
    ):
        user = await make_user()
        one = await make_transcript(user, title="Sprint planning")
        two = await make_transcript(user, title="Vendor renewal")
        seg_one = await make_segment(one, text="in sprint planning")
        seg_two = await make_segment(two, text="in vendor renewal")
        await make_segment_embedding(seg_one, embedding=_vec(1.0))
        await make_segment_embedding(seg_two, embedding=_vec(1.0))

        result = await _service(db_session).search(user.id, _vec(1.0), transcript_id=one.id)
        assert [r["segmentId"] for r in result["results"]] == [seg_one.id]


class TestHitShape:
    async def test_a_hit_carries_transcript_title_speaker_and_timestamp(
        self, make_user, make_transcript, make_speaker, make_segment, make_segment_embedding, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user, title="Q3 budget sync")
        speaker = await make_speaker(transcript, label="Speaker 1", display_name="Priya Raman")
        segment = await make_segment(
            transcript, text="let's ship in October", start_ms=154_300, speaker=speaker,
        )
        await make_segment_embedding(segment, embedding=_vec(1.0))

        result = await _service(db_session).search(user.id, _vec(1.0))
        hit = result["results"][0]
        assert hit["transcriptTitle"] == "Q3 budget sync"
        assert hit["speakerLabel"] == "Priya Raman"
        assert hit["timestampLabel"] == "2:34"
        assert hit["text"] == "let's ship in October"

    async def test_context_before_and_after_come_from_adjacent_segments(
        self, make_user, make_transcript, make_segment, make_segment_embedding, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        before = await make_segment(transcript, text="before text", start_ms=0)
        middle = await make_segment(transcript, text="middle text", start_ms=1000)
        after = await make_segment(transcript, text="after text", start_ms=2000)
        await make_segment_embedding(middle, embedding=_vec(1.0))

        result = await _service(db_session).search(user.id, _vec(1.0))
        hit = result["results"][0]
        assert hit["contextBefore"] == "before text"
        assert hit["contextAfter"] == "after text"
        assert before.text and after.text  # sanity: fixtures actually distinct

    async def test_context_is_null_at_a_transcript_boundary(
        self, make_user, make_transcript, make_segment, make_segment_embedding, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        only = await make_segment(transcript, text="the only segment", start_ms=0)
        await make_segment_embedding(only, embedding=_vec(1.0))

        result = await _service(db_session).search(user.id, _vec(1.0))
        hit = result["results"][0]
        assert hit["contextBefore"] is None
        assert hit["contextAfter"] is None
