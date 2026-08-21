"""Integration tests for finalizing a live tab-audio capture — SPEC-013.

The capture transport itself (the WebSocket relay, SPEC-014 §3) is untouched by this
feature and already has no dedicated test file of its own — this covers the one genuinely
new piece: turning a browser's accumulated utterances into a real `Transcript` and its
`Segment` rows, through the same `persist_segments()` the upload pipeline uses.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select
from vowcraft_db import Segment, Speaker, Transcript

from app.core.exceptions import AppError
from app.services.live_capture import _segments_from_utterances, finalize_live_capture


def _service_call(db_session, user_id, request_id="req-1", **overrides):
    body = dict(title=None, utterances=[{"text": "hello there", "atMs": 1000}], duration_ms=1000, provider=None)
    body.update(overrides)
    return finalize_live_capture(db_session, user_id, request_id, **body)


class TestSegmentsFromUtterances:
    def test_a_single_utterance_starts_at_zero(self):
        segments = _segments_from_utterances([{"text": "hello", "atMs": 500}])
        assert segments == [{"startMs": 0, "endMs": 500, "text": "hello", "speakerLabel": None, "words": []}]

    def test_monotonically_increasing_timestamps_chain_without_gaps_or_overlap(self):
        segments = _segments_from_utterances(
            [{"text": "first", "atMs": 1000}, {"text": "second", "atMs": 2500}]
        )
        assert segments[0]["startMs"] == 0
        assert segments[0]["endMs"] == 1000
        assert segments[1]["startMs"] == 1000
        assert segments[1]["endMs"] == 2500

    def test_out_of_order_input_is_sorted_first(self):
        segments = _segments_from_utterances(
            [{"text": "second", "atMs": 2000}, {"text": "first", "atMs": 1000}]
        )
        assert [s["text"] for s in segments] == ["first", "second"]

    def test_a_duplicate_or_non_increasing_timestamp_never_produces_a_zero_length_segment(self):
        segments = _segments_from_utterances(
            [{"text": "first", "atMs": 1000}, {"text": "second", "atMs": 1000}]
        )
        assert segments[1]["endMs"] > segments[1]["startMs"]

    def test_a_blank_utterance_is_dropped(self):
        segments = _segments_from_utterances([{"text": "   ", "atMs": 500}, {"text": "real", "atMs": 1000}])
        assert len(segments) == 1
        assert segments[0]["text"] == "real"

    def test_empty_input_yields_no_segments(self):
        assert _segments_from_utterances([]) == []


class TestFinalizeLiveCapture:
    async def test_creates_a_transcript_with_tab_capture_source(self, make_user, db_session):
        user = await make_user()
        with patch("app.services.live_capture.start_extraction_and_embedding"):
            transcript = await _service_call(db_session, user.id)
        assert transcript.source_type == "TAB_CAPTURE"
        assert transcript.diarized is False
        assert transcript.stage == "extracting"

    async def test_persists_segments_matching_the_utterances(self, make_user, db_session):
        user = await make_user()
        with patch("app.services.live_capture.start_extraction_and_embedding"):
            transcript = await _service_call(
                db_session, user.id,
                utterances=[{"text": "first thing said", "atMs": 1000}, {"text": "second thing said", "atMs": 2000}],
                duration_ms=2000,
            )
        rows = (
            await db_session.scalars(
                select(Segment).where(Segment.transcript_id == transcript.id).order_by(Segment.start_ms)
            )
        ).all()
        assert [r.text for r in rows] == ["first thing said", "second thing said"]

    async def test_creates_no_speaker_rows(self, make_user, db_session):
        user = await make_user()
        with patch("app.services.live_capture.start_extraction_and_embedding"):
            transcript = await _service_call(db_session, user.id)
        rows = (await db_session.scalars(select(Speaker).where(Speaker.transcript_id == transcript.id))).all()
        assert rows == []

    async def test_rejects_an_empty_utterance_list_and_creates_no_transcript(self, make_user, db_session):
        user = await make_user()
        before = len((await db_session.scalars(select(Transcript))).all())
        try:
            await _service_call(db_session, user.id, utterances=[{"text": "   ", "atMs": 100}])
            raise AssertionError("expected an empty_transcript AppError")
        except AppError as exc:
            assert exc.status_code == 422
            assert exc.code == "empty_transcript"
        after = len((await db_session.scalars(select(Transcript))).all())
        assert after == before

    async def test_invokes_extraction_and_embedding_for_the_new_transcript(self, make_user, db_session):
        user = await make_user()
        with patch("app.services.live_capture.start_extraction_and_embedding") as started:
            transcript = await _service_call(db_session, user.id)
        started.assert_called_once()
        assert started.call_args.args[0] == transcript.id
        assert started.call_args.args[1] == user.id

    async def test_a_missing_title_gets_a_generated_one(self, make_user, db_session):
        user = await make_user()
        with patch("app.services.live_capture.start_extraction_and_embedding"):
            transcript = await _service_call(db_session, user.id, title=None)
        assert transcript.title
        assert "Live capture" in transcript.title

    async def test_a_supplied_title_is_used_verbatim(self, make_user, db_session):
        user = await make_user()
        with patch("app.services.live_capture.start_extraction_and_embedding"):
            transcript = await _service_call(db_session, user.id, title="Sprint planning")
        assert transcript.title == "Sprint planning"
