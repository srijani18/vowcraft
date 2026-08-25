"""Enable/disable on a stored credential — SPEC-004 §3.

The interesting case, and the reason this exists: `shares_key_with` lets one Groq account
serve both transcription and extraction, so the user pastes the key once. But `enabled` lives
on the *row*, and one row serves both entries — so disabling "Groq — Whisper" also disabled
"Groq — Llama / Qwen" and silently broke action-item extraction. Someone following the
documented way to get speaker diarization on uploads lost extraction as a side effect.

Disabling now copies the key into any dependent's own row first, so "disable this one" means
that and nothing else.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.credentials import CredentialService

_TEST_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def api_settings() -> Settings:
    return Settings(APP_ENCRYPTION_KEY=_TEST_KEY)


def _service(db_session, settings) -> CredentialService:
    return CredentialService(db_session, settings)


class TestSharedKeyIndependence:
    async def test_disabling_transcription_leaves_extraction_working(
        self, make_user, db_session, api_settings
    ):
        """The regression this whole module exists for."""
        user = await make_user()
        service = _service(db_session, api_settings)
        await service.save(user.id, "groq", {"apiKey": "gsk_shared"}, request_id="r")

        # Extraction has no row of its own — it borrows via `shares_key_with`.
        assert (await service.resolve(user.id, "groq_llm")).api_key == "gsk_shared"

        await service.set_enabled(user.id, "groq", False, "r")

        assert (await service.resolve(user.id, "groq")).api_key is None, "transcription should be off"
        assert (
            await service.resolve(user.id, "groq_llm")
        ).api_key == "gsk_shared", "extraction lost its key"

    async def test_the_dependent_gets_its_own_row(self, make_user, db_session, api_settings):
        """Not a special case in `resolve`, but a real row — so the two are independent from
        here on and re-enabling transcription does not re-couple them."""
        user = await make_user()
        service = _service(db_session, api_settings)
        await service.save(user.id, "groq", {"apiKey": "gsk_shared"}, request_id="r")

        result = await service.set_enabled(user.id, "groq", False, "r")

        assert "Groq — Llama / Qwen" in result["preservedFor"]
        views = await service.list_views(user.id)
        extraction = next(v for v in views if v["service"] == "groq_llm")
        assert extraction["source"] == "USER"
        assert extraction["sharedFrom"] is None, "still borrowing rather than owning"

    async def test_an_existing_dependent_row_is_not_overwritten(
        self, make_user, db_session, api_settings
    ):
        """A dependent that already has its own key is already independent — copying over it
        would replace a deliberate choice with a different provider's key."""
        user = await make_user()
        service = _service(db_session, api_settings)
        await service.save(user.id, "groq", {"apiKey": "gsk_transcription"}, request_id="r")
        await service.save(user.id, "groq_llm", {"apiKey": "gsk_own_extraction"}, request_id="r")

        result = await service.set_enabled(user.id, "groq", False, "r")

        assert result["preservedFor"] == []
        assert (
            await service.resolve(user.id, "groq_llm")
        ).api_key == "gsk_own_extraction"

    async def test_nothing_is_copied_when_there_is_no_dependent(
        self, make_user, db_session, api_settings
    ):
        """Deepgram is nobody's shared source; disabling it should not invent rows."""
        user = await make_user()
        service = _service(db_session, api_settings)
        await service.save(user.id, "deepgram", {"apiKey": "dg-key"}, request_id="r")

        result = await service.set_enabled(user.id, "deepgram", False, "r")

        assert result["preservedFor"] == []


class TestToggling:
    async def test_disabling_makes_the_key_unresolvable(
        self, make_user, db_session, api_settings
    ):
        user = await make_user()
        service = _service(db_session, api_settings)
        await service.save(user.id, "deepgram", {"apiKey": "dg-key"}, request_id="r")

        await service.set_enabled(user.id, "deepgram", False, "r")

        assert (await service.resolve(user.id, "deepgram")).api_key is None

    async def test_re_enabling_restores_it_without_re_entering_the_key(
        self, make_user, db_session, api_settings
    ):
        """The point of a toggle over delete: there is no read path for a stored secret, so
        deleting and re-adding would mean finding the key again."""
        user = await make_user()
        service = _service(db_session, api_settings)
        await service.save(user.id, "deepgram", {"apiKey": "dg-key"}, request_id="r")
        await service.set_enabled(user.id, "deepgram", False, "r")

        await service.set_enabled(user.id, "deepgram", True, "r")

        assert (await service.resolve(user.id, "deepgram")).api_key == "dg-key"

    async def test_toggling_a_service_with_no_stored_key_is_404(
        self, make_user, db_session, api_settings
    ):
        user = await make_user()

        with pytest.raises(AppError) as excinfo:
            await _service(db_session, api_settings).set_enabled(user.id, "deepgram", False, "r")

        assert excinfo.value.status_code == 404

    async def test_an_unknown_service_is_404(self, make_user, db_session, api_settings):
        user = await make_user()

        with pytest.raises(AppError) as excinfo:
            await _service(db_session, api_settings).set_enabled(user.id, "nope", False, "r")

        assert excinfo.value.status_code == 404
