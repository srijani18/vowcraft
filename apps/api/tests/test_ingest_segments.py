"""Regression pin for ``persist_segments`` — extracted out of ``pipeline.py`` unchanged
(SPEC-013) so the upload pipeline and live capture write `Speaker`/`Segment`/`Word` rows
through identical code. This confirms the extraction didn't change behavior: the bundled
sample fixture's own output is exactly what the pre-extraction inline block was fed.
"""

from __future__ import annotations

from sqlalchemy import select
from vowcraft_db import Segment, Speaker, Word

from app.services.ingest.segments import persist_segments
from app.services.transcription import sample_transcribe


class TestPersistSegments:
    async def test_matches_the_sample_fixtures_own_shape(self, make_user, make_transcript, db_session):
        user = await make_user()
        transcript = await make_transcript(user)
        result = await sample_transcribe()

        await persist_segments(db_session, transcript.id, result["segments"])

        speakers = (
            await db_session.scalars(select(Speaker).where(Speaker.transcript_id == transcript.id))
        ).all()
        segments = (
            await db_session.scalars(
                select(Segment).where(Segment.transcript_id == transcript.id).order_by(Segment.start_ms)
            )
        ).all()
        expected_labels = {s["speakerLabel"] for s in result["segments"] if s.get("speakerLabel")}
        assert {sp.label for sp in speakers} == expected_labels
        assert len(segments) == len(result["segments"])
        assert [s.text for s in segments] == [s["text"] for s in result["segments"]]
        assert all(s.speaker_id is not None for s in segments)  # the sample fixture is diarized

        words = (
            await db_session.scalars(
                select(Word).where(Word.segment_id.in_([s.id for s in segments]))
            )
        ).all()
        expected_word_count = sum(len(s.get("words", [])) for s in result["segments"])
        assert len(words) == expected_word_count

    async def test_re_running_replaces_rather_than_accumulates(self, make_user, make_transcript, db_session):
        user = await make_user()
        transcript = await make_transcript(user)
        result = await sample_transcribe()

        await persist_segments(db_session, transcript.id, result["segments"])
        await persist_segments(db_session, transcript.id, result["segments"])

        segments = (
            await db_session.scalars(select(Segment).where(Segment.transcript_id == transcript.id))
        ).all()
        assert len(segments) == len(result["segments"])
