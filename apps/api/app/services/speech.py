"""Streaming ASR, relayed through this service — SPEC-014 §3.

    Browser ──WebSocket──> FastAPI ──WebSocket──> Deepgram
    Browser <──transcript── FastAPI <──partial──── Deepgram

The relay, rather than letting the browser talk to the vendor directly. That earlier design
existed only because Next.js route handlers cannot hold a WebSocket and Vercel's serverless
runtime cannot host one; FastAPI on Render can, so the constraint is gone and the relay is
better on every axis that matters:

* **The provider key never leaves this process.** Not the long-lived key, and not a
  short-lived one either — there is nothing to mint and nothing to leak.
* **The browser learns no vendor specifics.** No auth mode, no frame shape, no container
  format. Swapping Deepgram for a self-hosted Whisper changes this file and nothing in the
  frontend. (The previous design leaked exactly one vendor detail — that Deepgram wants its
  token in a subprotocol rather than a query parameter — and it cost a debugging session.)
* **Frames are normalised here**, so the client reads one shape forever.
* **Transcript can be persisted as it arrives**, which is what makes an interrupted session
  resumable (SPEC-014 §2.1).

The cost is that audio flows through this service, so it is bandwidth and a live connection
per speaker. That is the trade being made deliberately.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Protocol

import websockets
from websockets.exceptions import InvalidStatus

from app.core.config import Settings
from app.core.logging import Logger
from app.services.credentials import CredentialService


class SpeechUnavailable(Exception):
    """No usable provider. Carries a sentence written for the user."""

    def __init__(self, message: str, code: str = "streaming_unavailable") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Utterance:
    """One normalised result frame. The only transcript shape the client ever sees."""

    text: str
    is_final: bool
    #: Deepgram's diarization index for whoever spoke this, or None when the provider does
    #: not diarize (WhisperLive) or has not decided yet. Deliberately the raw provider
    #: integer rather than a "Speaker 1" string: labelling is a presentation choice made
    #: once, where segments are built, rather than in three places along the way.
    speaker: Optional[int] = None


class SpeechProvider(Protocol):
    """An upstream streaming-ASR provider.

    ``stream`` receives audio chunks and yields utterances. Implementations own their own
    transport, auth and frame shape entirely — none of it is visible past this interface,
    which is what keeps the client provider-agnostic by construction rather than by
    discipline.
    """

    id: str
    display_name: str
    model: str

    async def availability(self, user_id: str) -> tuple[bool, Optional[str]]: ...

    def stream(
        self, user_id: str, audio: asyncio.Queue[Optional[bytes]], log: Logger
    ) -> AsyncIterator[Utterance]: ...


class DeepgramProvider:
    """Deepgram Nova over WebSocket."""

    id = "deepgram"
    display_name = "Deepgram Nova 3"
    model = "nova-3"
    brand = "Deepgram"
    self_hosted = False
    blurb = "Lowest streaming latency, 30+ languages, built-in punctuation."

    _SOCKET = "wss://api.deepgram.com/v1/listen"
    _NEEDS_KEY = (
        "Live transcription needs a Deepgram key. Add one in Settings → API keys — it comes "
        "with $200 of free credit."
    )

    def __init__(self, credentials: CredentialService) -> None:
        self.credentials = credentials

    async def availability(self, user_id: str) -> tuple[bool, Optional[str]]:
        resolved = await self.credentials.resolve(user_id, "deepgram")
        return (True, None) if resolved.api_key else (False, self._NEEDS_KEY)

    def _url(self, encoding: Optional[str], sample_rate: Optional[int]) -> str:
        params = {
            "model": self.model,
            # Interim results are the point of a live surface; punctuation and smart
            # formatting make the running transcript readable rather than a lowercase wall.
            "interim_results": "true",
            "punctuate": "true",
            "smart_format": "true",
            "language": "multi",
            # Live meetings are the case that needs this most — several people, and a
            # transcript that attributes none of it. Deepgram diarizes on the streaming
            # endpoint too, so the only thing that was missing was asking.
            "diarize": "true",
        }
        # Container-wrapped audio (WebM/Opus from MediaRecorder) is auto-detected. Raw PCM
        # is not, and must declare itself or Deepgram silently returns nothing.
        if encoding:
            params["encoding"] = encoding
        if sample_rate:
            params["sample_rate"] = str(sample_rate)
        from urllib.parse import urlencode

        return f"{self._SOCKET}?{urlencode(params)}"

    async def stream(
        self,
        user_id: str,
        audio: asyncio.Queue,
        log: Logger,
        *,
        encoding: Optional[str] = None,
        sample_rate: Optional[int] = None,
    ) -> AsyncIterator[Utterance]:
        resolved = await self.credentials.resolve(user_id, "deepgram")
        api_key = resolved.api_key
        if not api_key:
            raise SpeechUnavailable(self._NEEDS_KEY)

        # Subprotocol auth, not a query parameter: the listen endpoint answers 401 to
        # `?token=` and `?access_token=`. Verified against the live API. It is also the
        # better choice — a credential in a query string lands in every access log.
        try:
            upstream = await websockets.connect(
                self._url(encoding, sample_rate),
                subprotocols=["token", api_key],  # type: ignore[list-item]
                open_timeout=15,
                # Deepgram is chatty; a generous frame budget avoids truncation on a long
                # interim result.
                max_size=2**22,
            )
        except InvalidStatus as exc:
            status = getattr(exc.response, "status_code", None)
            log.error("speech.upstream_rejected", provider=self.id, status=status)
            if status in (401, 403):
                raise SpeechUnavailable(
                    f"{self.brand} rejected the API key. Check it in Settings → API keys.",
                    code="speech_key_rejected",
                ) from exc
            raise SpeechUnavailable(
                f"{self.brand} refused the connection (HTTP {status}). Try again.",
                code="speech_upstream_refused",
            ) from exc
        except (OSError, asyncio.TimeoutError) as exc:
            log.error("speech.upstream_unreachable", provider=self.id, err=exc)
            raise SpeechUnavailable(
                f"{self.brand} could not be reached. Check your connection and try again.",
                code="speech_unreachable",
            ) from exc

        async def pump_audio() -> None:
            """Browser → Deepgram, until the client signals end of stream."""
            try:
                while True:
                    chunk = await audio.get()
                    if chunk is None:
                        # Tells Deepgram to flush the final result for the last utterance
                        # rather than dropping the tail of the closing sentence.
                        await upstream.send(json.dumps({"type": "CloseStream"}))
                        return
                    await upstream.send(chunk)
            except websockets.ConnectionClosed:
                return

        pump = asyncio.create_task(pump_audio())
        try:
            async for raw in upstream:
                if isinstance(raw, bytes):
                    continue
                try:
                    frame: Any = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if frame.get("type") != "Results":
                    # Metadata, keep-alives and speech-started events are common and are
                    # not transcripts. Skipping silently is the normal path.
                    continue
                alt = (frame.get("channel") or {}).get("alternatives") or [{}]
                text = (alt[0] or {}).get("transcript") or ""
                if not text.strip():
                    continue
                # The speaker sits on the words, not the alternative, and a single
                # utterance is speaker-contiguous in practice — so the first word that
                # carries one answers for the frame. Absent on interim frames sometimes,
                # which is why it stays Optional rather than defaulting to 0 (a real
                # speaker index) and mislabelling everything as the first speaker.
                words = (alt[0] or {}).get("words") or []
                speaker = next(
                    (w.get("speaker") for w in words if w.get("speaker") is not None), None
                )
                yield Utterance(
                    text=text,
                    is_final=bool(frame.get("is_final")),
                    speaker=int(speaker) if speaker is not None else None,
                )
        finally:
            pump.cancel()
            await upstream.close()


class WhisperLiveProvider:
    """A self-hosted WhisperLive-compatible service.

    Present so the interface stays honest: it has no credential, a configured URL, a
    different audio format and a different frame shape. Anything that only works for
    Deepgram fails here, which is the point of keeping a second implementation.
    """

    id = "whisper_live"
    display_name = "Self-hosted Whisper (live)"
    model = "whisper-small"
    brand = "your Whisper service"
    self_hosted = True
    blurb = "Streams to a Whisper service you run. No audio leaves your network."

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def availability(self, user_id: str) -> tuple[bool, Optional[str]]:
        if not self.settings.SPEECH_WS_URL:
            return False, (
                "Self-hosted live transcription needs SPEECH_WS_URL pointing at a "
                "WhisperLive-compatible service. Unset, so this provider is not offered."
            )
        return True, None

    async def stream(
        self, user_id: str, audio: asyncio.Queue, log: Logger, **_: Any
    ) -> AsyncIterator[Utterance]:
        url = self.settings.SPEECH_WS_URL
        if not url:
            raise SpeechUnavailable(
                "Self-hosted live transcription is not configured on this deployment."
            )
        try:
            upstream = await websockets.connect(url, open_timeout=15, max_size=2**22)
        except (OSError, asyncio.TimeoutError, InvalidStatus) as exc:
            log.error("speech.upstream_unreachable", provider=self.id, err=exc)
            raise SpeechUnavailable(
                "The self-hosted transcription service could not be reached.",
                code="speech_unreachable",
            ) from exc

        async def pump_audio() -> None:
            try:
                while True:
                    chunk = await audio.get()
                    if chunk is None:
                        await upstream.send(json.dumps({"type": "CloseStream"}))
                        return
                    await upstream.send(chunk)
            except websockets.ConnectionClosed:
                return

        pump = asyncio.create_task(pump_audio())
        try:
            async for raw in upstream:
                if isinstance(raw, bytes):
                    continue
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # A different shape entirely from Deepgram's — flat, differently named.
                text = frame.get("text") or ""
                if not text.strip():
                    continue
                yield Utterance(text=text, is_final=bool(frame.get("completed")))
        finally:
            pump.cancel()
            await upstream.close()


class SpeechRegistry:
    """Provider selection. Self-hosted first under ``auto``: a deployment that runs its own
    ASR would be surprised to have audio sent to a vendor instead."""

    def __init__(self, credentials: CredentialService, settings: Settings) -> None:
        self.settings = settings
        self._providers = [WhisperLiveProvider(settings), DeepgramProvider(credentials)]

    @property
    def providers(self) -> list[Any]:
        return list(self._providers)

    async def resolve(self, user_id: str) -> Any:
        configured = self.settings.SPEECH_PROVIDER
        if configured != "auto":
            for provider in self._providers:
                if provider.id == configured:
                    available, reason = await provider.availability(user_id)
                    if not available:
                        raise SpeechUnavailable(reason or "That provider is not configured.")
                    return provider
            raise SpeechUnavailable(f"Unknown speech provider “{configured}”.")

        for provider in self._providers:
            available, _ = await provider.availability(user_id)
            if available:
                return provider

        # Prefer the vendor's reason: "set SPEECH_WS_URL" is not the actionable advice for
        # someone who intended to use Deepgram.
        _, reason = await self._providers[-1].availability(user_id)
        raise SpeechUnavailable(reason or "No streaming transcription provider is configured.")

    async def status(self, user_id: str) -> dict[str, Any]:
        candidates = []
        chosen = None
        for provider in self._providers:
            available, reason = await provider.availability(provider_user_id := user_id)
            candidates.append(
                {
                    "id": provider.id,
                    "displayName": provider.display_name,
                    "blurb": provider.blurb,
                    "selfHosted": provider.self_hosted,
                    "available": available,
                    "reason": reason,
                }
            )
            eligible = self.settings.SPEECH_PROVIDER in ("auto", provider.id)
            if eligible and available and chosen is None:
                chosen = provider
        del provider_user_id

        if chosen is None:
            pinned = next(
                (c for c in candidates if c["id"] == self.settings.SPEECH_PROVIDER), None
            )
            reason = (pinned or {}).get("reason") or next(
                (c["reason"] for c in candidates if not c["selfHosted"]), None
            )
            return {
                "available": False,
                "provider": None,
                "displayName": None,
                "model": None,
                "selfHosted": False,
                "reason": reason or "No streaming transcription provider is configured.",
                "candidates": candidates,
            }

        return {
            "available": True,
            "provider": chosen.id,
            "displayName": chosen.display_name,
            "model": chosen.model,
            "selfHosted": chosen.self_hosted,
            "reason": None,
            "candidates": candidates,
        }
