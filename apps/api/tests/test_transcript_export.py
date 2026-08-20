"""Transcript export — SPEC-012 §6, §9.8.

The fixture and every assertion here are a direct port of ``tests/export.test.ts``
(the TypeScript ground truth), not independently authored — the whole point is that the
two implementations produce output a reader (or a subtitle player) cannot tell apart.
A prior port of ``render_export`` diverged from every one of these on the Markdown and
WebVTT formats specifically; see the audit referenced in SPEC-010's cutover notes.
"""

from __future__ import annotations

import re

from app.services.transcript_export import EXPORT_FORMATS, export_filename, render_export

TRANSCRIPT = {
    "id": "t1",
    "title": "Q3 budget sync",
    "status": "READY",
    "stage": "done",
    "progress": 100,
    "language": "en",
    "durationMs": 125_400,
    "diarized": True,
    "recordedAt": "2026-08-20T09:00:00.000Z",
    "createdAt": "2026-08-20T09:05:00.000Z",
    "transcribeProvider": "groq",
    "extractProvider": "groq_llm",
    "transcribeError": None,
    "extractError": None,
    "extractErrorCode": None,
    "extractModel": "openai/gpt-oss-120b",
    "summary": "Reviewed the budget and held the vendor renewal.",
    "counts": {"segments": 2, "actionItems": 3, "decisions": 1},
    "speakers": [{"id": "s1", "label": "Speaker 1", "displayName": "Marcus"}],
    "segments": [
        {
            "id": "seg1", "startMs": 61_000, "endMs": 65_200,
            "text": "Let's lock the budget review for Friday morning.",
            "speakerLabel": "Marcus", "speakerId": "s1", "words": [],
        },
        {
            "id": "seg2", "startMs": 3_723_456, "endMs": 3_725_000,
            "text": "Agreed.", "speakerLabel": None, "speakerId": None, "words": [],
        },
    ],
}


class TestSrt:
    srt = render_export(TRANSCRIPT, "srt")

    def test_timings_match_the_stored_segment_boundaries_exactly(self):
        # 61000ms -> 00:01:01,000 and 65200ms -> 00:01:05,200. An off-by-one here
        # desynchronises subtitles against the audio, which is the one thing SRT must
        # get right.
        assert re.search(r"00:01:01,000 --> 00:01:05,200", self.srt)

    def test_hours_are_carried_correctly_past_the_one_hour_mark(self):
        # 3,723,456ms is 1h 2m 3.456s — the case a naive minutes-only formatter gets wrong.
        assert re.search(r"01:02:03,456 --> 01:02:05,000", self.srt)

    def test_cues_are_numbered_from_one_and_separated_by_a_blank_line(self):
        assert re.match(r"^1\n00:01:01,000", self.srt)
        assert re.search(r"\n\n2\n", self.srt)

    def test_a_speaker_prefix_is_included_when_there_is_one_and_omitted_when_not(self):
        assert re.search(r"Marcus: Let's lock", self.srt)
        assert re.search(r"\n\nAgreed\.\n|\nAgreed\.\n", self.srt)
        assert "null:" not in self.srt, "an unattributed segment must not be prefixed"


class TestWebVtt:
    vtt = render_export(TRANSCRIPT, "vtt")

    def test_starts_with_the_required_signature(self):
        assert re.match(r"^WEBVTT\n", self.vtt)

    def test_uses_a_decimal_point_not_a_comma_the_only_difference_from_srt(self):
        assert re.search(r"00:01:01\.000 --> 00:01:05\.200", self.vtt)
        assert "00:01:01,000" not in self.vtt, "a comma makes it invalid VTT"

    def test_cues_are_numbered_like_srt_not_left_out(self):
        assert re.match(r"^WEBVTT\n\n1\n00:01:01\.000", self.vtt)

    def test_speakers_are_a_plain_prefix_not_a_v_voice_span(self):
        # A port choice worth pinning explicitly: the TS original never used `<v Name>`
        # voice spans for this format — same "Name: text" convention as SRT and TXT.
        assert "Marcus: Let's lock" in self.vtt
        assert "<v " not in self.vtt


class TestTextAndMarkdown:
    def test_text_carries_readable_timestamps_and_speaker_names(self):
        txt = render_export(TRANSCRIPT, "txt")
        assert re.search(r"\[1:01\] Marcus: Let's lock the budget review", txt)
        assert re.match(r"^Q3 budget sync", txt)

    def test_markdown_carries_the_metadata_and_the_summary(self):
        md = render_export(TRANSCRIPT, "md")
        assert re.match(r"^# Q3 budget sync", md)
        assert re.search(r"\*\*Duration\*\* 2:05", md)
        assert re.search(r"\*\*Language\*\* en", md)
        assert re.search(r"\*\*Recorded\*\* 2026-08-20", md)
        assert re.search(r"\*\*Transcribed by\*\* groq", md)
        assert re.search(r"> Reviewed the budget", md)

    def test_markdown_states_when_speakers_were_not_separated(self):
        # Silence here would imply the attribution is trustworthy when it is absent.
        md = render_export({**TRANSCRIPT, "diarized": False}, "md")
        assert re.search(r"\*\*Speakers\*\* not separated", md)

    def test_markdown_omits_the_speakers_line_when_diarized(self):
        md = render_export(TRANSCRIPT, "md")
        assert "not separated" not in md


class TestFilenamesAndFormats:
    def test_a_title_becomes_a_safe_filename(self):
        assert export_filename("Q3 budget sync", "srt") == "q3-budget-sync.srt"
        assert export_filename("Weekly / standup!! ", "txt") == "weekly-standup.txt"

    def test_an_unusable_title_still_yields_a_filename(self):
        assert export_filename("///", "md") == "transcript.md"
        assert export_filename("", "vtt") == "transcript.vtt"

    def test_every_format_declares_a_mime_type(self):
        for fmt, mime_type in EXPORT_FORMATS.items():
            assert mime_type, f"{fmt} has no mime type"

    def test_pdf_and_docx_are_deliberately_absent(self):
        assert "pdf" not in EXPORT_FORMATS
        assert "docx" not in EXPORT_FORMATS
