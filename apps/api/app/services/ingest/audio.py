"""FFmpeg demux — SPEC-010 §4.1.

The audio track is extracted from video before anything is sent to a transcription
provider, so a provider's size cap bounds the *audio*, not the video. On a screen
recording the video track is almost all of the bytes; sending that to a provider that only
reads the audio would waste most of a request's size budget on data it discards anyway.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.exceptions import unprocessable
from app.core.logging import logger

_TARGET_RATE = 16_000
_TARGET_BITRATE = "48k"
_PROBE_TIMEOUT_S = 30
_TRANSCODE_TIMEOUT_S = 10 * 60

# Bounded so a malformed input cannot make FFmpeg's diagnostics eat memory.
_OUTPUT_CAP = 256 * 1024

_ffmpeg_available: Optional[bool] = None


async def _run(command: list[str], timeout_s: int) -> str:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{command[0]} timed out after {timeout_s}s")

    if process.returncode != 0:
        raise RuntimeError(
            f"{command[0]} exited {process.returncode}: {stderr[-500:].decode(errors='replace')}"
        )
    return stdout[:_OUTPUT_CAP].decode(errors="replace")


async def ffmpeg_available() -> bool:
    global _ffmpeg_available
    if _ffmpeg_available is not None:
        return _ffmpeg_available
    try:
        await _run(["ffmpeg", "-version"], _PROBE_TIMEOUT_S)
        _ffmpeg_available = True
    except Exception:  # noqa: BLE001 — absence is a normal deployment state, not an error
        _ffmpeg_available = False
    return _ffmpeg_available


@dataclass
class ProbeResult:
    duration_ms: Optional[int]
    has_audio: bool
    has_video: bool
    audio_codec: Optional[str]


async def probe(path: str) -> ProbeResult:
    try:
        stdout = await _run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            _PROBE_TIMEOUT_S,
        )
        data = json.loads(stdout)
        streams = data.get("streams") or []
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        try:
            duration = float(data.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            duration = None
        return ProbeResult(
            duration_ms=round(duration * 1000) if duration is not None else None,
            has_audio=audio is not None,
            has_video=any(s.get("codec_type") == "video" for s in streams),
            audio_codec=(audio or {}).get("codec_name"),
        )
    except Exception as exc:  # noqa: BLE001 — a probe failure degrades, it does not abort
        logger.warn("audio.probe_failed", err=exc)
        return ProbeResult(None, False, False, None)


@dataclass
class ExtractionOutcome:
    data: bytes
    filename: str
    mime_type: str
    transcoded: bool
    duration_ms: Optional[int]
    original_bytes: int


async def extract_audio(
    *, data: bytes, filename: str, mime_type: str, container: str, is_video: bool, max_bytes: int
) -> ExtractionOutcome:
    needs_transcode = (
        is_video
        # QuickTime is ISO-BMFF like MP4, but providers reject the brand outright.
        or container == "mov"
        or len(data) > max_bytes
    )
    if not needs_transcode:
        return ExtractionOutcome(
            data=data, filename=filename, mime_type=mime_type, transcoded=False,
            duration_ms=None, original_bytes=len(data),
        )

    if not await ffmpeg_available():
        raise unprocessable(
            "ffmpeg_unavailable",
            (
                "This deployment cannot extract audio from video: FFmpeg is not installed. "
                "Upload an audio file (MP3, M4A, WAV, OGG, FLAC), or run the Docker image, "
                "which includes it."
            )
            if is_video or container == "mov"
            else (
                "That file is over the 25 MB provider limit and FFmpeg is not installed to "
                "compress it. Upload a shorter recording, or run the Docker image, which "
                "includes FFmpeg."
            ),
        )

    with tempfile.TemporaryDirectory(prefix="v2b-audio-") as tmp:
        source = str(Path(tmp) / f"source.{container}")
        target = str(Path(tmp) / "audio.mp3")
        Path(source).write_bytes(data)

        probed = await probe(source)
        if not probed.has_audio:
            raise unprocessable(
                "no_audio_track",
                "That video has no audio track, so there is nothing to transcribe."
                if probed.has_video
                else "No audio stream was found in that file.",
            )

        await _run(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", source,
                # Drop video and subtitles explicitly; on a screen recording the video
                # track is almost all of the bytes.
                "-vn", "-sn", "-dn",
                "-ac", "1",
                "-ar", str(_TARGET_RATE),
                "-b:a", _TARGET_BITRATE,
                "-map_metadata", "-1",
                "-f", "mp3",
                "-y", target,
            ],
            _TRANSCODE_TIMEOUT_S,
        )

        transcoded = Path(target).read_bytes()

        if len(transcoded) == 0:
            raise unprocessable("extraction_empty", "Audio extraction produced an empty file.")

        if len(transcoded) > max_bytes:
            # 48 kbps mono is already frugal; past this the recording is genuinely too
            # long for a single request, and chunking is the answer rather than more
            # compression.
            minutes = round(probed.duration_ms / 60_000) if probed.duration_ms else None
            mb = len(transcoded) / 1024 / 1024
            raise unprocessable(
                "audio_too_long",
                f"Even after extracting and compressing the audio"
                f"{f' ({minutes} minutes)' if minutes else ''}, it is {mb:.1f} MB — over the "
                "25 MB the transcription providers accept. Split the recording and upload "
                "the parts.",
            )

        return ExtractionOutcome(
            data=transcoded,
            filename=Path(filename).stem + ".mp3",
            mime_type="audio/mpeg",
            transcoded=True,
            # From the *source* probe, not the re-encoded target: ffmpeg read the real
            # container, whereas re-probing the transcoded MP3 reports what survived
            # re-encoding, which can drift slightly from the original.
            duration_ms=probed.duration_ms,
            original_bytes=len(data),
        )
