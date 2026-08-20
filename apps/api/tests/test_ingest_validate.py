"""Upload validation and title derivation — SPEC-010 §4.

Every detector case here was verified against the TypeScript implementation's magic-byte
table before being written down.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import AppError
from app.services.ingest.validate import detect_media, title_from_filename, validate_upload


def _pad(header: bytes, length: int = 32) -> bytes:
    return header + bytes(max(0, length - len(header)))


class TestMagicByteDetection:
    def test_wav(self):
        d = detect_media(_pad(b"RIFF\x00\x00\x00\x00WAVEfmt "))
        assert d and d.mime_type == "audio/wav" and d.video is False

    def test_ogg(self):
        d = detect_media(_pad(b"OggS"))
        assert d and d.extension == "ogg"

    def test_flac(self):
        d = detect_media(_pad(b"fLaC"))
        assert d and d.extension == "flac"

    def test_webm_is_marked_as_potentially_video(self):
        """WebM can hold either; the pipeline resolves it properly once ffprobe has
        actually looked inside."""
        d = detect_media(_pad(bytes([0x1A, 0x45, 0xDF, 0xA3])))
        assert d and d.container == "webm" and d.video is True

    def test_mp3_by_id3_tag(self):
        d = detect_media(_pad(b"ID3"))
        assert d and d.extension == "mp3"

    def test_mp3_by_frame_sync(self):
        d = detect_media(_pad(bytes([0xFF, 0xE0])))
        assert d and d.extension == "mp3"

    def test_m4a_is_audio_only(self):
        d = detect_media(_pad(b"\x00\x00\x00\x18ftypM4A "))
        assert d and d.mime_type == "audio/mp4" and d.video is False

    def test_mov_is_video(self):
        d = detect_media(_pad(b"\x00\x00\x00\x18ftypqt  "))
        assert d and d.mime_type == "video/quicktime" and d.video is True

    def test_generic_mp4_is_video(self):
        d = detect_media(_pad(b"\x00\x00\x00\x18ftypisom"))
        assert d and d.mime_type == "video/mp4" and d.video is True

    def test_a_pdf_renamed_mp3_is_rejected_on_its_bytes(self):
        """An extension is a claim by the uploader; the first bytes are evidence."""
        assert detect_media(_pad(b"%PDF-1.7 not audio at all")) is None

    def test_too_short_to_have_a_signature(self):
        assert detect_media(b"\x00\x00\x00") is None

    def test_empty(self):
        assert detect_media(b"") is None


class TestValidateUpload:
    def test_an_empty_file_is_rejected(self):
        with pytest.raises(AppError) as exc:
            validate_upload("x.wav", b"")
        assert exc.value.code == "empty_file"

    def test_unrecognised_bytes_are_rejected(self):
        with pytest.raises(AppError) as exc:
            validate_upload("x.mp3", _pad(b"not a real media file"))
        assert exc.value.code == "unsupported_media"

    def test_a_valid_file_returns_its_checksum(self):
        data = _pad(b"OggS")
        result = validate_upload("x.ogg", data)
        assert len(result.checksum) == 64  # sha-256 hex
        assert result.detected.extension == "ogg"

    def test_the_same_bytes_produce_the_same_checksum(self):
        """The whole point: an identical re-upload must be detectable as identical."""
        data = _pad(b"fLaC")
        assert validate_upload("a.flac", data).checksum == validate_upload("b.flac", data).checksum

    def test_the_checksum_matches_the_typescript_implementation_byte_for_byte(self):
        """A regression pin: the TS side hashes the *base64 text* of the file
        (`sha256(file.bytes.toString('base64'))`, src/server/ingest/validate.ts), not the
        raw bytes. An earlier Python port hashed the raw bytes directly — same length,
        same-looking checksum, completely different value — so a recording uploaded once
        through each service was silently never recognised as identical by the other.

        Fixture captured from the live Node implementation, for the same 32-byte padded
        OGG header the other tests in this file already use:
            node -e "const d = Buffer.concat([Buffer.from('OggS'), Buffer.alloc(28)]);
              console.log(require('crypto').createHash('sha256')
                .update(d.toString('base64')).digest('hex'))"
        """
        result = validate_upload("x.ogg", _pad(b"OggS"))
        assert result.checksum == "7938b177c900f48b3731dff101d01a01962cd22e4538f8d01a7344d6283ff800"

    def test_the_returned_filename_uses_the_detected_extension_not_the_claimed_one(self):
        """A regression pin: an earlier port returned the filename verbatim, so a
        mislabelled upload (a real MOV saved with a .mp4 name, say) carried a lying
        extension into the temp file ffmpeg demuxes — a port of src/server/ingest/
        validate.ts's stem-plus-detected-extension normalisation."""
        result = validate_upload("recording.mp4", _pad(b"OggS"))
        assert result.filename == "recording.ogg"

    def test_the_stem_is_kept_but_capped_at_120_characters(self):
        long_stem = "a" * 200
        result = validate_upload(f"{long_stem}.mp4", _pad(b"OggS"))
        assert result.filename == ("a" * 120) + ".ogg"

    def test_an_empty_stem_falls_back_to_recording(self):
        result = validate_upload(".mp4", _pad(b"OggS"))
        assert result.filename == "recording.ogg"

    def test_audio_over_25mb_is_rejected(self):
        oversized = _pad(b"OggS", 26 * 1024 * 1024)
        with pytest.raises(AppError) as exc:
            validate_upload("big.ogg", oversized)
        assert exc.value.code == "file_too_large"

    def test_video_gets_the_500mb_ceiling_not_the_25mb_one(self):
        """The audio track is extracted before anything is sent to a provider, so the
        video ceiling bounds transcoding time, not the provider's request size."""
        just_under = _pad(b"\x00\x00\x00\x18ftypisom", 30 * 1024 * 1024)
        validate_upload("clip.mp4", just_under)  # must not raise


class TestTitleFromFilename:
    @pytest.mark.parametrize("filename,expected", [
        ("q3_budget-sync.wav", "Q3 budget sync"),
        ("Meeting Notes.mp3", "Meeting Notes"),  # only the first char is touched; existing capitals in the rest are left alone
        ("___.mp3", "Untitled recording"),
        ("recording.tar.gz", "Recording.tar"),
        ("a---b__c.wav", "A b c"),
    ])
    def test_derivation(self, filename, expected):
        assert title_from_filename(filename) == expected
