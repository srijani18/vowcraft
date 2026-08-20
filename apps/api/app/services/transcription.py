"""Whisper-shaped transcription providers — SPEC-010 §6.

One implementation covers every provider that speaks the Whisper `verbose_json` dialect
(Groq, OpenAI); adding another is a base URL, a model id and a credential service.

Whisper does not diarize. Every segment reports `speaker_label: None` rather than inventing
a "Speaker 1" — that would imply a single speaker when the truth is "not separated", and the
difference matters to a reader deciding how much to trust the transcript.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.services.credentials import CredentialService

_TIMEOUT_SECONDS = 600  # an hour of audio genuinely takes minutes to transcribe


class TranscriptionError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _sec_to_ms(value: Optional[float]) -> int:
    return max(0, round((value or 0) * 1000))


#: Reported language names → BCP-47. Providers are inconsistent about returning a code
#: versus a name, so both are handled.
_LANGUAGE_CODES: dict[str, str] = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de", "italian": "it",
    "portuguese": "pt", "dutch": "nl", "russian": "ru", "polish": "pl", "turkish": "tr",
    "ukrainian": "uk", "romanian": "ro", "swedish": "sv", "norwegian": "no", "danish": "da",
    "finnish": "fi", "czech": "cs", "greek": "el", "hungarian": "hu", "hebrew": "he",
    "arabic": "ar", "persian": "fa", "urdu": "ur", "hindi": "hi", "bengali": "bn",
    "tamil": "ta", "telugu": "te", "marathi": "mr", "gujarati": "gu", "kannada": "kn",
    "malayalam": "ml", "punjabi": "pa", "nepali": "ne", "sinhala": "si",
    "chinese": "zh", "mandarin": "zh", "cantonese": "yue", "japanese": "ja", "korean": "ko",
    "vietnamese": "vi", "thai": "th", "indonesian": "id", "malay": "ms", "tagalog": "tl",
    "swahili": "sw", "afrikaans": "af", "catalan": "ca", "basque": "eu", "galician": "gl",
    "welsh": "cy", "icelandic": "is", "latvian": "lv", "lithuanian": "lt", "estonian": "et",
    "slovak": "sk", "slovenian": "sl", "croatian": "hr", "serbian": "sr", "bulgarian": "bg",
    "macedonian": "mk", "albanian": "sq", "armenian": "hy", "georgian": "ka",
    "azerbaijani": "az", "kazakh": "kk", "uzbek": "uz", "mongolian": "mn", "burmese": "my",
    "khmer": "km", "lao": "lo",
}

_BCP47 = None  # compiled lazily to avoid an import-time regex cost on a rarely-hit path


def normalise_language(reported: Optional[str]) -> Optional[str]:
    global _BCP47
    if not reported or not reported.strip():
        return None
    value = reported.strip()
    if _BCP47 is None:
        import re

        _BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
    # Already a code — 'en', 'EN', 'en-GB', 'zh-Hans'. Case-insensitive on the primary
    # subtag too: providers are not consistent about it, and 'EN' is still a code.
    if _BCP47.match(value):
        return value.lower()
    return _LANGUAGE_CODES.get(value.lower(), value)


@dataclass(frozen=True)
class TranscriptionProvider:
    id: str
    display_name: str
    base_url: str
    model: str
    credential_service: str
    free_tier: bool
    max_bytes: int = 25 * 1024 * 1024


PROVIDERS: tuple[TranscriptionProvider, ...] = (
    TranscriptionProvider(
        "groq", "Groq (Whisper large-v3 turbo)", "https://api.groq.com/openai/v1",
        "whisper-large-v3-turbo", "groq", True,
    ),
    TranscriptionProvider(
        "openai_audio", "OpenAI (Whisper)", "https://api.openai.com/v1",
        "whisper-1", "openai", False,
    ),
)

#: Not in `PROVIDERS` — a port of src/server/transcription/sample.ts. It exists so the
#: *entire* pipeline (upload, word timings, extraction, guardrails, the board) is
#: exercisable on a fresh instance with no API key at all; without it, `docker compose up`
#: shows a form that immediately needs a credential rather than a working demo. Never
#: silent about being a fixture: the transcript records `transcribeProvider = 'sample'`.
_SAMPLE_PROVIDER = TranscriptionProvider(
    "sample", "Bundled sample (no API key)", "", "fixture", "local_whisper", True,
    max_bytes=2**63 - 1,
)

# Written to exercise the extractor's harder cases rather than to flatter it: a superseded
# action, a hedged low-confidence one, an explicit decision, an external recipient, and a
# deadline that only resolves against "now". Kept byte-identical to the TS fixture script.
_SAMPLE_SCRIPT: tuple[tuple[str, int, str], ...] = (
    ("Speaker 1", 4_000, "Right, three things today: the Q3 budget, the vendor renewal, and the analyst role."),
    ("Speaker 2", 12_500, "On the budget — I'll get the revised numbers over to Priya by Friday."),
    ("Speaker 1", 21_000, "Good. Let's lock a review for Friday morning, say ten, with Priya and Jordan."),
    ("Speaker 3", 31_500, "Works for me. I'll want the deck the day before if possible."),
    ("Speaker 1", 39_000, "Noted. Marcus, can you set up a meeting with Peter about the renewal?"),
    ("Speaker 2", 47_000, "Sure. Actually, make that Peter and Jordan — Jordan owns the contract now."),
    ("Speaker 3", 56_000, "We are holding the vendor contract until legal signs off. That's decided."),
    ("Speaker 1", 65_500, "Agreed. Nothing moves on the renewal before legal clears it."),
    ("Speaker 2", 73_000, "Someone should probably email the vendor about the delay, I think."),
    ("Speaker 1", 82_000, "Let's hold that until we know the new date. I'll write up what we agreed and send it round."),
    ("Speaker 3", 92_500, "One more — we're not opening the analyst role until Q4 at the earliest."),
    ("Speaker 1", 101_000, "Understood. And honestly, the roadmap order feels right to me as it stands."),
    ("Speaker 2", 110_000, "Last thing: the offsite. Maybe we should look at venues at some point."),
    ("Speaker 1", 118_500, "Park it. Let's not add work we haven't committed to. That's us."),
)


def _sample_segment(speaker: str, at_ms: int, text: str) -> dict[str, Any]:
    """A port of sample.ts::wordsFor — distributes word timings evenly across a line at
    ~155 wpm, a normal meeting pace. Plausible, and honest about being synthetic."""
    tokens = text.split(" ")
    per_word = round(60_000 / 155)
    end_ms = at_ms + len(tokens) * per_word
    return {
        "startMs": at_ms,
        "endMs": end_ms,
        "text": text,
        "speakerLabel": speaker,
        "words": [
            {
                "text": token,
                "startMs": at_ms + i * per_word,
                "endMs": at_ms + (i + 1) * per_word,
                "confidence": 0.94,
            }
            for i, token in enumerate(tokens)
        ],
    }


async def sample_transcribe() -> dict[str, Any]:
    # A short delay so the polling UI and its progress states are genuinely exercised
    # rather than completing before the first poll — matches the TS fixture exactly.
    await asyncio.sleep(0.7)
    segments = [_sample_segment(speaker, at_ms, text) for speaker, at_ms, text in _SAMPLE_SCRIPT]
    return {
        "language": "en",
        "durationMs": (segments[-1]["endMs"] if segments else 0) + 2_000,
        "segments": segments,
        # The fixture *does* carry speaker labels, which is the one way it flatters
        # reality — real Whisper output does not.
        "diarized": True,
        "provider": "sample",
        "model": "fixture",
    }


async def resolve_transcriber(
    user_id: str, credentials: CredentialService, *, allow_sample: bool = False
) -> tuple[TranscriptionProvider, str, str]:
    """The first configured provider and its key, or the bundled sample as a last resort.

    A port of src/server/transcription/registry.ts::resolveTranscriber. Returns
    ``(provider, api_key, source)`` where source is ``"USER" | "ENV" | "SAMPLE"``.
    """
    for provider in PROVIDERS:
        resolved = await credentials.resolve(user_id, provider.credential_service)
        if resolved.api_key:
            return provider, resolved.api_key, resolved.source

    if allow_sample:
        return _SAMPLE_PROVIDER, "", "SAMPLE"

    from app.core.exceptions import service_unavailable

    raise service_unavailable(
        "transcription_unavailable",
        "No transcription provider is configured. Add a Groq key in Settings → API keys — its "
        "free tier covers roughly eight hours of audio a day and needs no card.",
        {
            "needs": [
                {"service": p.credential_service, "displayName": p.display_name, "freeTier": p.free_tier}
                for p in PROVIDERS
            ]
        },
    )


async def transcription_status(user_id: str, credentials: CredentialService) -> dict[str, Any]:
    """A port of src/server/transcription/registry.ts::transcriptionStatus."""
    candidates = []
    chosen: Optional[tuple[TranscriptionProvider, str]] = None
    for provider in PROVIDERS:
        resolved = await credentials.resolve(user_id, provider.credential_service)
        configured = bool(resolved.api_key)
        candidates.append(
            {
                "service": provider.credential_service,
                "displayName": provider.display_name,
                "freeTier": provider.free_tier,
                "configured": configured,
            }
        )
        if configured and chosen is None:
            chosen = (provider, resolved.source)
    return {
        "available": chosen is not None,
        "provider": chosen[0].display_name if chosen else None,
        "source": chosen[1] if chosen else None,
        "candidates": candidates,
    }


async def transcribe(
    provider: TranscriptionProvider,
    *,
    data: bytes,
    filename: str,
    mime_type: str,
    api_key: str,
    language: Optional[str] = None,
) -> dict[str, Any]:
    files = {"file": (filename, data, mime_type)}
    form = {
        "model": provider.model,
        # verbose_json plus word granularity is what yields the per-word timings the
        # transcript player and the Word table are built around.
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language:
        form["language"] = language

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{provider.base_url}/audio/transcriptions",
                headers={"authorization": f"Bearer {api_key}"},
                data=form, files=files,
            )
    except httpx.TimeoutException as exc:
        raise TranscriptionError(
            "timeout", f"{provider.display_name} did not finish within 10 minutes.", True
        ) from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError(
            "network_error", f"{provider.display_name} was unreachable.", True
        ) from exc

    if response.status_code >= 400:
        body = response.text
        detail = body[:300]
        try:
            import json as _json

            detail = _json.loads(body).get("error", {}).get("message", detail)
        except Exception:  # noqa: BLE001 — fall back to the raw slice
            pass
        raise TranscriptionError(
            f"{provider.id}_http_{response.status_code}",
            (
                f"{provider.display_name} rejected the API key. Check it in Settings → API keys."
                if response.status_code == 401
                else f"{provider.display_name} rate limit reached. Its free tier resets daily — "
                     "try again later."
                if response.status_code == 429
                else f"{provider.display_name} returned {response.status_code}: {detail}"
            ),
            response.status_code in (408, 429, 500, 502, 503, 504),
        )

    body = response.json()
    segments: list[dict[str, Any]] = []
    all_words = body.get("words") or []

    if body.get("segments"):
        for segment in body["segments"]:
            start_ms = _sec_to_ms(segment.get("start"))
            end_ms = _sec_to_ms(segment.get("end"))
            nested = segment.get("words") or [
                w for w in all_words
                if start_ms <= _sec_to_ms(w.get("start")) and _sec_to_ms(w.get("end")) <= end_ms
            ]
            segments.append(
                {
                    "startMs": start_ms,
                    "endMs": end_ms,
                    "text": (segment.get("text") or "").strip(),
                    "speakerLabel": None,
                    "words": [
                        {
                            "text": (w.get("word") or "").strip(),
                            "startMs": _sec_to_ms(w.get("start")),
                            "endMs": _sec_to_ms(w.get("end")),
                            "confidence": w.get("probability"),
                        }
                        for w in nested
                        if (w.get("word") or "").strip()
                    ],
                }
            )
    elif (body.get("text") or "").strip():
        segments.append(
            {
                "startMs": 0, "endMs": _sec_to_ms(body.get("duration")),
                "text": body["text"].strip(), "speakerLabel": None, "words": [],
            }
        )

    if not segments:
        raise TranscriptionError(
            "empty_transcript",
            "The provider returned no speech. The recording may be silent or too quiet.",
        )

    return {
        "language": normalise_language(body.get("language")),
        "durationMs": _sec_to_ms(body.get("duration")) if body.get("duration") else segments[-1]["endMs"],
        "segments": segments,
        "diarized": False,
        "provider": provider.id,
        "model": provider.model,
    }
