"""Upload validation — SPEC-010 §4.

The type is decided by magic bytes, never the filename: an extension is a claim by whoever
uploaded the file, and the first bytes are evidence. A `.mp3` that is actually a PDF is
refused here rather than sent to a transcription provider that would reject it with a far
less useful error.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from app.core.exceptions import bad_request, unprocessable

MAX_AUDIO_BYTES = 25 * 1024 * 1024
#: The audio track is extracted before anything is sent to a provider (§ extract_audio), so
#: this ceiling is not the provider's limit — it exists to bound how long a video can take
#: to transcode, not how large the useful part of it can be.
MAX_VIDEO_BYTES = 500 * 1024 * 1024


@dataclass(frozen=True)
class DetectedMedia:
    mime_type: str
    extension: str
    container: str
    #: Whether the container *carries* a video track. WebM can hold either audio or video,
    #: so it is marked true and resolved properly once ffprobe has actually looked inside —
    #: see `extract_audio`.
    video: bool


def _ascii(data: bytes, offset: int, length: int) -> str:
    return data[offset : offset + length].decode("latin-1", errors="replace")


def _starts_with(data: bytes, signature: bytes, offset: int = 0) -> bool:
    return data[offset : offset + len(signature)] == signature


def detect_media(data: bytes) -> Optional[DetectedMedia]:
    if len(data) < 12:
        return None
    if _ascii(data, 0, 4) == "RIFF" and _ascii(data, 8, 4) == "WAVE":
        return DetectedMedia("audio/wav", "wav", "wav", False)
    if _ascii(data, 0, 4) == "OggS":
        return DetectedMedia("audio/ogg", "ogg", "ogg", False)
    if _ascii(data, 0, 4) == "fLaC":
        return DetectedMedia("audio/flac", "flac", "flac", False)
    if _starts_with(data, bytes([0x1A, 0x45, 0xDF, 0xA3])):
        return DetectedMedia("audio/webm", "webm", "webm", True)
    if _ascii(data, 0, 3) == "ID3":
        return DetectedMedia("audio/mpeg", "mp3", "mp3", False)
    if data[0] == 0xFF and len(data) > 1 and (data[1] & 0xE0) == 0xE0:
        return DetectedMedia("audio/mpeg", "mp3", "mp3", False)
    # ISO base media: ....ftyp<brand>
    if _ascii(data, 4, 4) == "ftyp":
        brand = _ascii(data, 8, 4)
        # M4A/M4B are audio-only; qt and mp4 brands carry video we let ffmpeg strip.
        if brand.startswith(("M4A", "M4B")):
            return DetectedMedia("audio/mp4", "m4a", "m4a", False)
        if brand.startswith("qt"):
            return DetectedMedia("video/quicktime", "mov", "mov", True)
        return DetectedMedia("video/mp4", "mp4", "mp4", True)
    return None


@dataclass
class ValidatedUpload:
    data: bytes
    detected: DetectedMedia
    filename: str
    checksum: str


def validate_upload(filename: str, data: bytes) -> ValidatedUpload:
    if len(data) == 0:
        raise bad_request("empty_file", "That file is empty.")

    detected = detect_media(data)
    if detected is None:
        raise unprocessable(
            "unsupported_media",
            "That does not look like an audio or video file. Accepted: MP3, M4A, WAV, WebM, "
            "OGG, FLAC, MP4, MOV. The type is checked by reading the file, not by its "
            "extension.",
        )

    ceiling = MAX_VIDEO_BYTES if detected.video else MAX_AUDIO_BYTES
    if len(data) > ceiling:
        mb = len(data) / 1024 / 1024
        limit_mb = ceiling / 1024 / 1024
        raise unprocessable(
            "file_too_large",
            f"That file is {mb:.1f} MB, over the {limit_mb:.0f} MB limit for "
            f"{'video' if detected.video else 'audio'}.",
        )

    # Keep the user's stem so the transcript title stays recognisable, but use the
    # extension the bytes actually justify — a `.mov` that ffprobe reads as MP4 becomes
    # `.mp4`, so the name told to ffmpeg later matches what it really contains.
    stem = re.sub(r"\.[^.]+$", "", filename)[:120] or "recording"
    normalised_filename = f"{stem}.{detected.extension}"

    return ValidatedUpload(
        data=data,
        detected=detected,
        filename=normalised_filename,
        # A port of src/server/ingest/validate.ts's `sha256(file.bytes.toString('base64'))`
        # exactly: hashing the base64 *text*, not the raw bytes. An unusual choice, but
        # cross-implementation dedup (the same recording uploaded once through each
        # service) silently fails unless both sides hash the identical representation.
        checksum=hashlib.sha256(base64.b64encode(data)).hexdigest(),
    )


def title_from_filename(name: str) -> str:
    import re

    stem = re.sub(r"\.[^.]+$", "", name)
    stem = re.sub(r"[_-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return "Untitled recording"
    return stem[0].upper() + stem[1:]
