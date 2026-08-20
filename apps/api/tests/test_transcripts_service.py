"""Integration tests for ``TranscriptService`` against the real schema — SPEC-010, SPEC-012.

Before this file, nothing exercised this service against a real database at all (per the
transcripts/ingest contract audit) — every one of these assertions exists because the
comparison against the TypeScript original and against ``scripts/smoke.sh`` found a real
divergence at that exact spot.
"""

from __future__ import annotations

from app.core.exceptions import AppError
from app.services.transcripts import TranscriptService


def _service(db_session) -> TranscriptService:
    return TranscriptService(db_session)


class TestListAndGet:
    async def test_list_reports_counts_and_fields(
        self, make_user, make_transcript, make_segment, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user, title="Q3 planning")
        await make_segment(transcript)
        await make_segment(transcript, start_ms=1000, end_ms=2000)

        result = await _service(db_session).list_transcripts(user.id)

        assert len(result["transcripts"]) == 1
        row = result["transcripts"][0]
        assert row["title"] == "Q3 planning"
        assert row["counts"]["segments"] == 2
        assert row["counts"]["actionItems"] == 0
        assert row["counts"]["decisions"] == 0

    async def test_list_is_scoped_to_the_requesting_user(self, make_user, make_transcript, db_session):
        me = await make_user(email="me@acme.test")
        someone_else = await make_user(email="other@acme.test")
        await make_transcript(me, title="Mine")
        await make_transcript(someone_else, title="Not mine")

        result = await _service(db_session).list_transcripts(me.id)

        assert [t["title"] for t in result["transcripts"]] == ["Mine"]

    async def test_get_returns_segments_speakers_and_words_in_order(
        self, make_user, make_transcript, make_speaker, make_segment, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        speaker = await make_speaker(transcript, label="Speaker 1")
        await make_segment(transcript, start_ms=5000, end_ms=6000, text="Second", speaker=speaker)
        await make_segment(transcript, start_ms=0, end_ms=1000, text="First", speaker=speaker)

        result = await _service(db_session).get_transcript(user.id, transcript.id)

        assert [s["text"] for s in result["segments"]] == ["First", "Second"]
        assert result["segments"][0]["speakerId"] == speaker.id
        assert result["segments"][0]["speakerLabel"] == "Speaker 1"

    async def test_speakers_are_ordered_by_label(
        self, make_user, make_transcript, make_speaker, db_session
    ):
        # A regression pin: an earlier port had no ORDER BY here at all, so the order
        # was whatever the database felt like on a given day — the frontend indexes
        # `speakers[0]` and expects it to be stable.
        user = await make_user()
        transcript = await make_transcript(user)
        await make_speaker(transcript, label="Speaker 3")
        await make_speaker(transcript, label="Speaker 1")
        await make_speaker(transcript, label="Speaker 2")

        result = await _service(db_session).get_transcript(user.id, transcript.id)

        assert [s["label"] for s in result["speakers"]] == ["Speaker 1", "Speaker 2", "Speaker 3"]

    async def test_get_missing_transcript_is_404(self, make_user, db_session):
        user = await make_user()
        try:
            await _service(db_session).get_transcript(user.id, "does-not-exist")
            raise AssertionError("expected an AppError")
        except AppError as exc:
            assert exc.code == "not_found"


class TestRenameSpeakers:
    async def test_a_batch_rename_updates_every_speaker_and_returns_them_ordered(
        self, make_user, make_transcript, make_speaker, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        alice = await make_speaker(transcript, label="Speaker 2")
        bob = await make_speaker(transcript, label="Speaker 1")

        result = await _service(db_session).rename_speakers(
            user.id, transcript.id,
            [(alice.id, "Alice"), (bob.id, "Bob")],
        )

        assert result["ok"] is True
        by_id = {s["id"]: s for s in result["speakers"]}
        assert by_id[alice.id]["displayName"] == "Alice"
        assert by_id[bob.id]["displayName"] == "Bob"
        # Label ("Speaker N") is never touched — the machine's original attribution
        # survives a rename so a re-transcription can still be matched against it.
        assert by_id[alice.id]["label"] == "Speaker 2"
        assert [s["id"] for s in result["speakers"]] == [bob.id, alice.id]  # ordered by label

    async def test_a_null_display_name_clears_the_override(
        self, make_user, make_transcript, make_speaker, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        speaker = await make_speaker(transcript, display_name="Old Name")

        result = await _service(db_session).rename_speakers(user.id, transcript.id, [(speaker.id, None)])

        assert result["speakers"][0]["displayName"] is None

    async def test_a_whitespace_only_display_name_also_clears_the_override(
        self, make_user, make_transcript, make_speaker, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        speaker = await make_speaker(transcript, display_name="Old Name")

        result = await _service(db_session).rename_speakers(user.id, transcript.id, [(speaker.id, "   ")])

        assert result["speakers"][0]["displayName"] is None

    async def test_a_missing_transcript_is_404_not_unknown_speaker(self, make_user, db_session):
        # A regression pin: an earlier port answered this with `unknown_speaker` (422)
        # because it looked up transcript-and-speaker in one query — a missing
        # transcript and an unowned speaker id produced the identical response, which
        # points a caller investigating a 404-shaped problem at the wrong code entirely.
        user = await make_user()
        try:
            await _service(db_session).rename_speakers(user.id, "does-not-exist", [("spk_1", "Name")])
            raise AssertionError("expected an AppError")
        except AppError as exc:
            assert exc.code == "not_found"

    async def test_a_speaker_from_another_transcript_is_rejected(
        self, make_user, make_transcript, make_speaker, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        other_transcript = await make_transcript(user, title="A different recording")
        foreign_speaker = await make_speaker(other_transcript)

        try:
            await _service(db_session).rename_speakers(
                user.id, transcript.id, [(foreign_speaker.id, "Name")]
            )
            raise AssertionError("expected an AppError")
        except AppError as exc:
            assert exc.code == "unknown_speaker"

    async def test_one_unknown_id_in_a_batch_rejects_the_whole_batch(
        self, make_user, make_transcript, make_speaker, db_session
    ):
        # All-or-nothing, matching the TS route's single $transaction: a batch is not
        # partially applied.
        user = await make_user()
        transcript = await make_transcript(user)
        real = await make_speaker(transcript)

        try:
            await _service(db_session).rename_speakers(
                user.id, transcript.id, [(real.id, "Real"), ("does-not-exist", "Fake")]
            )
            raise AssertionError("expected an AppError")
        except AppError as exc:
            assert exc.code == "unknown_speaker"

        # The real speaker's rename must not have gone through either.
        result = await _service(db_session).get_transcript(user.id, transcript.id)
        assert result["speakers"][0]["displayName"] is None


class TestDelete:
    async def test_delete_removes_the_transcript(self, make_user, make_transcript, db_session):
        user = await make_user()
        transcript = await make_transcript(user)

        await _service(db_session).delete_transcript(user.id, transcript.id, "req-1")

        try:
            await _service(db_session).get_transcript(user.id, transcript.id)
            raise AssertionError("expected the transcript to be gone")
        except AppError as exc:
            assert exc.code == "not_found"

    async def test_delete_of_a_missing_transcript_is_404(self, make_user, db_session):
        user = await make_user()
        try:
            await _service(db_session).delete_transcript(user.id, "does-not-exist", "req-1")
            raise AssertionError("expected an AppError")
        except AppError as exc:
            assert exc.code == "not_found"


class TestAudio:
    async def test_audio_returns_the_stored_bytes(
        self, make_user, make_transcript, make_asset, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_asset(transcript, data=b"real-audio-bytes", mime_type="audio/mpeg")

        data, mime_type, filename = await _service(db_session).audio(user.id, transcript.id)

        assert data == b"real-audio-bytes"
        assert mime_type == "audio/mpeg"

    async def test_audio_missing_is_404(self, make_user, make_transcript, db_session):
        user = await make_user()
        transcript = await make_transcript(user)
        try:
            await _service(db_session).audio(user.id, transcript.id)
            raise AssertionError("expected an AppError")
        except AppError as exc:
            assert exc.code == "not_found"
