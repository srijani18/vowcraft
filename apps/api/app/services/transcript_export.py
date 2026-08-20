"""Transcript export — SPEC-012 §6.

TXT, Markdown, SRT and WebVTT. PDF and DOCX are deliberately absent and say so in the error
rather than being quietly missing: generating them well needs a layout engine, and generating
them badly is worse than not offering them.
"""

from __future__ import annotations

import re
from typing import Any, Optional

EXPORT_FORMATS: dict[str, str] = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}


def _stamp(ms: int, decimal: str) -> str:
    """`HH:MM:SS,mmm` for SRT, `HH:MM:SS.mmm` for VTT — the one difference between them."""
    total, millis = divmod(max(0, ms), 1000)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal}{millis:03d}"


def _clock(ms: int) -> str:
    total = max(0, ms) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _speaker_prefix(label: Optional[str]) -> str:
    return f"{label}: " if label else ""


def render_export(transcript: dict[str, Any], fmt: str) -> str:
    """A port of src/server/ingest/export.ts::renderExport, format for format."""
    segments = transcript["segments"]

    if fmt == "txt":
        title = transcript["title"]
        body = "\n\n".join(
            f"[{_clock(s['startMs'])}] {_speaker_prefix(s.get('speakerLabel'))}{s['text']}"
            for s in segments
        )
        return f"{title}\n{'=' * len(title)}\n\n{body}\n"

    if fmt == "md":
        meta = []
        if transcript.get("recordedAt"):
            meta.append(f"**Recorded** {transcript['recordedAt'][:10]}")
        if transcript.get("durationMs"):
            meta.append(f"**Duration** {_clock(transcript['durationMs'])}")
        if transcript.get("language"):
            meta.append(f"**Language** {transcript['language']}")
        if transcript.get("transcribeProvider"):
            meta.append(f"**Transcribed by** {transcript['transcribeProvider']}")
        # Stated rather than implied: unattributed segments are not one speaker.
        if not transcript.get("diarized"):
            meta.append("**Speakers** not separated")

        body = "\n\n".join(
            f"**[{_clock(s['startMs'])}]** {_speaker_prefix(s.get('speakerLabel'))}{s['text']}"
            for s in segments
        )
        return (
            f"# {transcript['title']}\n\n"
            + (f"{' · '.join(meta)}\n\n" if meta else "")
            + (f"> {transcript['summary']}\n\n" if transcript.get("summary") else "")
            + f"## Transcript\n\n{body}\n"
        )

    if fmt == "srt":
        blocks = [
            f"{i}\n{_stamp(s['startMs'], ',')} --> {_stamp(s['endMs'], ',')}\n"
            f"{_speaker_prefix(s.get('speakerLabel'))}{s['text']}"
            for i, s in enumerate(segments, start=1)
        ]
        return "\n\n".join(blocks) + "\n"

    if fmt == "vtt":
        # Plain "Speaker: text", not a `<v Name>` voice span — same prefix convention as
        # SRT and TXT — and numbered cues, both matching the TS original exactly.
        blocks = [
            f"{i}\n{_stamp(s['startMs'], '.')} --> {_stamp(s['endMs'], '.')}\n"
            f"{_speaker_prefix(s.get('speakerLabel'))}{s['text']}"
            for i, s in enumerate(segments, start=1)
        ]
        return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"

    raise ValueError(f"Unknown export format: {fmt}")


def export_filename(title: str, fmt: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return f"{slug or 'transcript'}.{fmt}"
