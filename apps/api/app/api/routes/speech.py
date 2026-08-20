"""Live transcription — SPEC-014 §3.

    GET  /api/speech/status   is it configured, and via which provider
    WS   /api/speech/stream   the relay: browser audio in, transcript out

**Authentication on a WebSocket.** A browser cannot set an ``Authorization`` header when
opening a WebSocket, so the token travels in the ``Sec-WebSocket-Protocol`` header via
``new WebSocket(url, ['bearer', accessToken])``. A query parameter would have worked and is
rejected here on purpose: query strings are recorded verbatim by proxies, load balancers and
access logs, and a bearer token in a log is a bearer token in an attacker's hands.

**The client protocol**, deliberately small — one JSON shape in each direction, with no
vendor concepts:

    → binary frames          audio, exactly as MediaRecorder produced it
    → {"type":"stop"}        stop capturing; flush the final utterance
    ← {"type":"ready", …}    upstream connected; provider named for display
    ← {"type":"transcript", "text":…, "isFinal":bool}
    ← {"type":"error", "code":…, "message":…}
    ← {"type":"closed", "reason":…}
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from vowcraft_db import User

from app.api.dependencies import CurrentUser, SessionDep, SettingsDep
from app.core.config import get_settings
from app.core.logging import logger
from app.core.security import TokenError, claims_match_user, decode_token
from app.db.session import session_factory
from app.services.credentials import CredentialService
from app.services.speech import SpeechRegistry, SpeechUnavailable

router = APIRouter()

#: Bound on buffered audio. A client that outruns the upstream is throttled by
#: back-pressure rather than allowed to grow the queue without limit — one slow provider
#: must not become this process running out of memory.
_AUDIO_QUEUE_MAX = 256


@router.get("/status")
async def status(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict:
    """Whether live transcription works and, when it does not, why.

    Mints nothing and connects to nothing, so the record button can state its own reason
    for being disabled instead of failing on click (SPEC-014 §3.2).
    """
    registry = SpeechRegistry(CredentialService(session, settings), settings)
    return await registry.status(user.id)


async def _authenticate(websocket: WebSocket) -> tuple[Optional[User], Optional[str]]:
    """Resolve the user from the WebSocket subprotocol.

    Returns ``(user, subprotocol_to_accept)``. The accepted subprotocol must be echoed back
    or the browser closes the connection immediately — a detail that is easy to miss and
    presents as a mysterious instant disconnect.
    """
    offered = websocket.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in offered.split(",") if p.strip()]
    if len(parts) < 2 or parts[0] != "bearer":
        return None, None
    token = parts[1]

    settings = get_settings()
    try:
        claims = decode_token(
            token,
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            expected_type="access",
        )
    except TokenError:
        return None, "bearer"

    async with session_factory()() as session:
        user = await session.scalar(select(User).where(User.id == claims.subject))
        if user is None or not claims_match_user(claims, user.password_updated_at):
            return None, "bearer"
        return user, "bearer"


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    user, subprotocol = await _authenticate(websocket)

    if user is None:
        # Accept, explain, then close. Rejecting the handshake outright gives the browser a
        # bare 403 with no readable body — which is exactly the uninformative failure that
        # cost a debugging session on the previous design. One frame of explanation is
        # worth the extra round trip.
        await websocket.accept(subprotocol=subprotocol)
        await websocket.send_json(
            {
                "type": "error",
                "code": "unauthenticated",
                "message": "Your session has expired. Reload the page and sign in again.",
            }
        )
        await websocket.close(code=1008)
        return

    await websocket.accept(subprotocol=subprotocol)
    log = logger.child(userId=user.id, surface="speech")
    settings = get_settings()

    async with session_factory()() as session:
        registry = SpeechRegistry(CredentialService(session, settings), settings)
        try:
            provider = await registry.resolve(user.id)
        except SpeechUnavailable as exc:
            log.warn("speech.unavailable", code=exc.code)
            await websocket.send_json({"type": "error", "code": exc.code, "message": exc.message})
            await websocket.close(code=1011)
            return

    audio: asyncio.Queue = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
    finals: list[str] = []

    async def receive_from_browser() -> None:
        """Browser → queue. Ends on `stop` or disconnect."""
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                await audio.put(None)
                return
            if (data := message.get("bytes")) is not None:
                await audio.put(data)
            elif (text := message.get("text")) is not None:
                # The only control message the client sends. Anything else is ignored
                # rather than treated as audio.
                if '"stop"' in text:
                    await audio.put(None)
                    return

    reader = asyncio.create_task(receive_from_browser())
    sent = 0
    try:
        await websocket.send_json(
            {
                "type": "ready",
                "provider": provider.id,
                "displayName": provider.display_name,
                "model": provider.model,
                "selfHosted": provider.self_hosted,
            }
        )
        async for utterance in provider.stream(user.id, audio, log):
            if utterance.is_final:
                finals.append(utterance.text.strip())
            sent += 1
            await websocket.send_json(
                {"type": "transcript", "text": utterance.text, "isFinal": utterance.is_final}
            )

        await websocket.send_json(
            {
                "type": "closed",
                "reason": "complete",
                # The accumulated final text, so a client that dropped a frame still ends
                # up with the whole transcript rather than a gap it cannot detect.
                "finalText": " ".join(t for t in finals if t),
            }
        )
    except SpeechUnavailable as exc:
        log.warn("speech.stream_failed", code=exc.code)
        with contextlib.suppress(RuntimeError):
            await websocket.send_json(
                {"type": "error", "code": exc.code, "message": exc.message}
            )
    except WebSocketDisconnect:
        # The user navigated away or closed the tab mid-sentence. Normal, not an error.
        log.info("speech.client_disconnected", frames=sent)
    except Exception as exc:  # noqa: BLE001 — a relay must not die on one bad session
        log.error("speech.stream_error", err=exc, frames=sent)
        with contextlib.suppress(RuntimeError):
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "speech_failed",
                    "message": "Transcription stopped unexpectedly. Anything already "
                    "transcribed is kept.",
                }
            )
    finally:
        reader.cancel()
        with contextlib.suppress(Exception):
            await websocket.close()
        log.info("speech.session_ended", frames=sent, finalUtterances=len(finals))
