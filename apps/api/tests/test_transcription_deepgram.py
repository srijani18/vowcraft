"""Deepgram transcription — SPEC-010 §3, and the diarization SPEC-011 never delivered.

Why this exists: the credentials catalogue has always advertised Deepgram as offering
"built-in diarization", but only Groq and OpenAI were ever implemented as transcribers —
both Whisper, which cannot attribute speech to a speaker at all. So adding a Deepgram key
did nothing, and every transcript came back with "speakers were not separated" no matter
what the user configured. Diarization did not need a new container; it needed the provider
the catalogue already sold.

Every request is stubbed. These pin the wire format and the speaker mapping, which is the
part that silently degrades: a transcript with the wrong speaker attributed to a sentence is
worse than one with none, because the reader presents it as fact.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.transcription import (
    PROVIDERS,
    TranscriptionError,
    transcribe,
)

DEEPGRAM = next(p for p in PROVIDERS if p.id == "deepgram")

#: Shaped like a real `utterances=true` response, trimmed to what the mapper reads.
BODY = {
    "metadata": {"duration": 12.5},
    "results": {
        "channels": [{"detected_language": "en", "alternatives": [{"transcript": "full text"}]}],
        "utterances": [
            {
                "start": 0.5, "end": 3.0, "speaker": 0, "transcript": "Shall we start?",
                "words": [
                    {"word": "shall", "punctuated_word": "Shall", "start": 0.5, "end": 0.9, "confidence": 0.99},
                    {"word": "we", "punctuated_word": "we", "start": 0.9, "end": 1.1, "confidence": 0.98},
                ],
            },
            {
                "start": 3.2, "end": 6.0, "speaker": 1, "transcript": "Yes, go ahead.",
                "words": [
                    {"word": "yes", "punctuated_word": "Yes,", "start": 3.2, "end": 3.6, "confidence": 0.97},
                ],
            },
        ],
    },
}


@pytest.fixture
def deepgram_stub(monkeypatch):
    def _install(body=BODY, status: int = 200):
        seen: dict = {}

        async def _post(self, url, **kwargs):  # noqa: ANN001, ANN003
            seen["url"] = url
            seen["params"] = kwargs.get("params") or {}
            seen["headers"] = kwargs.get("headers") or {}
            seen["content"] = kwargs.get("content")
            return httpx.Response(status, json=body, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", _post)
        return seen

    return _install


async def _run(**overrides):
    kwargs = dict(
        data=b"audio-bytes", filename="meeting.mp3", mime_type="audio/mpeg",
        api_key="dg-key", language=None,
    )
    kwargs.update(overrides)
    return await transcribe(DEEPGRAM, **kwargs)


class TestTheRequest:
    async def test_diarization_and_utterances_are_requested(self, deepgram_stub):
        """The whole point. Without `diarize` there are no speakers, and without
        `utterances` the response has no speaker-contiguous grouping to map onto segments."""
        seen = deepgram_stub()

        await _run()

        assert seen["params"]["diarize"] == "true"
        assert seen["params"]["utterances"] == "true"

    async def test_the_token_scheme_is_not_bearer(self, deepgram_stub):
        """Deepgram uses `Token`, not `Bearer` — the OpenAI-compatible path's header would
        simply 401 here."""
        seen = deepgram_stub()

        await _run()

        assert seen["headers"]["authorization"] == "Token dg-key"

    async def test_the_audio_is_the_raw_body_not_a_multipart_field(self, deepgram_stub):
        seen = deepgram_stub()

        await _run()

        assert seen["content"] == b"audio-bytes"
        assert seen["headers"]["content-type"] == "audio/mpeg"

    async def test_an_explicit_language_replaces_detection(self, deepgram_stub):
        seen = deepgram_stub()

        await _run(language="en")

        assert seen["params"]["language"] == "en"
        assert "detect_language" not in seen["params"]


class TestSpeakerMapping:
    async def test_speakers_are_numbered_from_one(self, deepgram_stub):
        """Deepgram numbers from 0; everything else in this system — the bundled sample, the
        reader's rename control — labels from 1. An off-by-one here would show the user
        "Speaker 0"."""
        deepgram_stub()

        result = await _run()

        assert [s["speakerLabel"] for s in result["segments"]] == ["Speaker 1", "Speaker 2"]

    async def test_punctuated_words_are_preferred_for_display(self, deepgram_stub):
        """`smart_format` puts the readable form in `punctuated_word`; the reader renders
        words verbatim, so using the raw token would strip all punctuation."""
        deepgram_stub()

        result = await _run()

        assert [w["text"] for w in result["segments"][0]["words"]] == ["Shall", "we"]

    async def test_word_timings_survive_as_milliseconds(self, deepgram_stub):
        """The Word table and the highlight loop are built on these."""
        deepgram_stub()

        result = await _run()

        first = result["segments"][0]["words"][0]
        assert (first["startMs"], first["endMs"]) == (500, 900)

    async def test_two_distinct_speakers_report_as_diarized(self, deepgram_stub):
        deepgram_stub()

        assert (await _run())["diarized"] is True

    async def test_a_single_speaker_does_not_claim_diarization(self, deepgram_stub):
        """Honest per response, not per provider: one label is a distinction without a
        difference to the reader, and claiming otherwise hides the "speakers were not
        separated" notice that would correctly appear."""
        body = {
            "metadata": {"duration": 4.0},
            "results": {
                "channels": [{"alternatives": [{"transcript": "just me"}]}],
                "utterances": [
                    {"start": 0.0, "end": 2.0, "speaker": 0, "transcript": "Just me here.", "words": []}
                ],
            },
        }
        deepgram_stub(body=body)

        assert (await _run())["diarized"] is False


class TestFailures:
    async def test_no_speech_is_reported_rather_than_returned_empty(self, deepgram_stub):
        deepgram_stub(body={"metadata": {}, "results": {"utterances": []}})

        with pytest.raises(TranscriptionError) as excinfo:
            await _run()

        assert excinfo.value.code == "empty_transcript"

    async def test_a_rejected_key_points_at_the_settings_page(self, deepgram_stub):
        deepgram_stub(body={"err_msg": "nope"}, status=401)

        with pytest.raises(TranscriptionError) as excinfo:
            await _run()

        assert "Settings → API keys" in excinfo.value.message
        assert excinfo.value.retryable is False

    async def test_a_rate_limit_is_retryable(self, deepgram_stub):
        deepgram_stub(body={}, status=429)

        with pytest.raises(TranscriptionError) as excinfo:
            await _run()

        assert excinfo.value.retryable is True
