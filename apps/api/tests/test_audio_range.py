"""``Range`` header handling for GET /api/transcripts/:id/audio — SPEC-012 §9.

A port of the regex-and-clamp logic in src/app/api/transcripts/[id]/audio/route.ts. An
earlier port of this endpoint never read the `Range` header at all — every request served
the full file and always answered 200, while still advertising `accept-ranges: bytes`, so
seeking in the player re-downloaded the whole recording instead of jumping to a byte offset.
"""

from __future__ import annotations

from app.api.routes.transcripts import resolve_range

TOTAL = 1000


class TestNoRangeToHonour:
    def test_absent_header_serves_the_full_body(self):
        assert resolve_range(None, TOTAL) is None

    def test_empty_header_serves_the_full_body(self):
        assert resolve_range("", TOTAL) is None

    def test_an_unrecognised_shape_serves_the_full_body(self):
        # Multi-range and the suffix form (`bytes=-500`, "last 500 bytes") are not
        # handled by this endpoint, same as the TS original's single-pattern match.
        assert resolve_range("bytes=0-99,200-299", TOTAL) is None
        assert resolve_range("items=0-99", TOTAL) is None


class TestSatisfiableRanges:
    def test_a_bounded_range_returns_its_exact_bounds(self):
        assert resolve_range("bytes=0-99", TOTAL) == (0, 99)

    def test_an_open_ended_range_extends_to_the_last_byte(self):
        assert resolve_range("bytes=500-", TOTAL) == (500, TOTAL - 1)

    def test_an_end_beyond_the_content_length_is_clamped(self):
        assert resolve_range("bytes=0-9999", TOTAL) == (0, TOTAL - 1)

    def test_a_single_byte_range_is_valid(self):
        assert resolve_range("bytes=5-5", TOTAL) == (5, 5)

    def test_the_last_byte_alone_is_valid(self):
        assert resolve_range(f"bytes={TOTAL - 1}-", TOTAL) == (TOTAL - 1, TOTAL - 1)


class TestUnsatisfiableRanges:
    """The sentinel — ``(None, total_length)`` — is what the route turns into 416."""

    def test_start_beyond_the_content_length_is_unsatisfiable(self):
        assert resolve_range(f"bytes={TOTAL}-", TOTAL) == (None, TOTAL)

    def test_start_after_end_is_unsatisfiable(self):
        assert resolve_range("bytes=500-100", TOTAL) == (None, TOTAL)

    def test_way_past_the_end_is_unsatisfiable(self):
        assert resolve_range(f"bytes={TOTAL + 500}-{TOTAL + 600}", TOTAL) == (None, TOTAL)
