"""Integration tests for ``CredentialService`` against the real schema — SPEC-004.

Before this file, the FastAPI port had zero coverage and a catalogue holding 9 of the
original's 25 services — missing every TRANSLATION and EMBEDDING entry and three of four
INTEGRATION ones, `verify` and `/status` were unimplemented, `sharedFrom` returned a raw
service id instead of the sibling's display name, and `DELETE` 404'd on a credential that
was never stored instead of answering idempotently. Every test below exists because that
comparison against ``src/lib/credentials/service.ts`` found a real divergence at that exact
spot.
"""

from __future__ import annotations

import base64
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.credentials import CATALOG, CredentialService, find_service

_TEST_KEY = base64.b64encode(os.urandom(32)).decode()


def _settings(**overrides) -> Settings:
    return Settings(**{"APP_ENCRYPTION_KEY": _TEST_KEY, **overrides})


def _service(db_session, settings=None) -> CredentialService:
    return CredentialService(db_session, settings or _settings())


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "{}"):
        self.status_code = status_code
        self.text = text
        self.reason_phrase = "OK" if status_code < 400 else "Error"

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def _mocked_http(response=None, exc=None):
    client = AsyncMock()
    if exc is not None:
        client.request.side_effect = exc
    else:
        client.request.return_value = response or _FakeResponse()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return patch("app.services.credentials.httpx.AsyncClient", return_value=client), client


class TestCatalogCompleteness:
    def test_every_module_is_represented(self):
        modules = {spec.module for spec in CATALOG}
        assert modules == {"TRANSCRIPTION", "EXTRACTION", "TRANSLATION", "EMBEDDING", "INTEGRATION"}

    def test_the_catalog_matches_the_frontends_service_for_service(self):
        """The two catalogues are the same contract in two languages, and a provider needs
        an entry in both (the frontend renders the form; this side resolves and verifies
        the key). This used to assert a hardcoded count of 31, which only caught a service
        *disappearing* — it could not catch the actual failure mode, which is one catalogue
        gaining an entry the other never hears about. Comparing the ids names the culprit.
        """
        import re
        from pathlib import Path

        ts_path = (
            Path(__file__).resolve().parents[3] / "src" / "lib" / "credentials" / "catalog.ts"
        )
        assert ts_path.is_file(), f"frontend catalogue not found at {ts_path}"
        # Only the `service:` key — `sharesKeyWith` and friends also hold service ids.
        frontend = set(re.findall(r"^\s*service: '([^']+)'", ts_path.read_text(), re.MULTILINE))
        backend = {spec.service for spec in CATALOG}

        assert frontend, "parsed no services out of catalog.ts — the regex has drifted"
        assert backend - frontend == set(), (
            f"in this catalogue but not the frontend's: {sorted(backend - frontend)}"
        )
        assert frontend - backend == set(), (
            f"in the frontend's catalogue but not this one: {sorted(frontend - backend)}"
        )

    def test_a_previously_missing_translation_service_exists(self):
        assert find_service("deepl") is not None

    def test_a_previously_missing_embedding_service_exists(self):
        assert find_service("voyage") is not None

    def test_a_previously_missing_integration_service_exists(self):
        assert find_service("sendgrid") is not None
        assert find_service("notion") is not None


class TestResolution:
    async def test_a_saved_key_resolves_as_user_sourced(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_realkey"})
        resolved = await service.resolve(user.id, "groq")
        assert resolved.source == "USER"
        assert resolved.secrets["apiKey"] == "gsk_realkey"

    async def test_an_unconfigured_service_resolves_to_none(self, db_session, make_user):
        user = await make_user()
        resolved = await _service(db_session).resolve(user.id, "cohere")
        assert resolved.source == "NONE"
        assert resolved.configured is False

    async def test_env_fallback_is_used_when_no_user_key_exists(self, db_session, make_user):
        user = await make_user()
        settings = _settings(CEREBRAS_API_KEY="env-cerebras-key")
        resolved = await _service(db_session, settings).resolve(user.id, "cerebras")
        assert resolved.source == "ENV"
        assert resolved.secrets["apiKey"] == "env-cerebras-key"

    async def test_a_user_key_wins_over_the_environment_by_default(self, db_session, make_user):
        user = await make_user()
        settings = _settings(GROQ_API_KEY="env-groq-key")
        service = _service(db_session, settings)
        await service.save(user.id, "groq", {"apiKey": "user-groq-key"})
        resolved = await service.resolve(user.id, "groq")
        assert resolved.source == "USER"
        assert resolved.secrets["apiKey"] == "user-groq-key"

    async def test_credentials_env_locked_inverts_the_precedence(self, db_session, make_user):
        user = await make_user()
        settings = _settings(GROQ_API_KEY="env-groq-key", CREDENTIALS_ENV_LOCKED=True)
        service = _service(db_session, settings)
        await service.save(user.id, "groq", {"apiKey": "user-groq-key"})
        resolved = await service.resolve(user.id, "groq")
        assert resolved.source == "ENV"
        assert resolved.secrets["apiKey"] == "env-groq-key"

    async def test_a_key_saved_under_one_sibling_resolves_for_the_other(self, db_session, make_user):
        # groq_llm shares groq's key — a real support problem this fixes: someone who
        # entered their key under "Transcription" must not be told extraction is
        # unconfigured.
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_shared"})
        resolved = await service.resolve(user.id, "groq_llm")
        assert resolved.source == "USER"
        assert resolved.secrets["apiKey"] == "gsk_shared"

    async def test_a_local_only_service_resolves_without_any_secret(self, db_session, make_user):
        user = await make_user()
        resolved = await _service(db_session).resolve(user.id, "local_whisper")
        assert resolved.source == "ENV"
        assert resolved.secrets == {}

    async def test_a_row_that_fails_to_decrypt_falls_back_to_env(self, db_session, make_user):
        user = await make_user()
        # Saved under one encryption key, resolved under another — the AAD/key mismatch
        # this simulates is exactly what a rotated APP_ENCRYPTION_KEY produces.
        await _service(db_session).save(user.id, "mistral", {"apiKey": "user-mistral-key"})
        other_settings = _settings(MISTRAL_API_KEY="env-mistral-key")
        object.__setattr__(other_settings, "APP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
        resolved = await _service(db_session, other_settings).resolve(user.id, "mistral")
        assert resolved.source == "ENV"
        assert resolved.secrets["apiKey"] == "env-mistral-key"


class TestListViews:
    async def test_an_unconfigured_multi_field_service_shows_all_its_fields(self, db_session, make_user):
        user = await make_user()
        views = await _service(db_session).list_views(user.id)
        azure = next(v for v in views if v["service"] == "azure_openai")
        assert {f["key"] for f in azure["fields"]} == {"apiKey", "endpoint", "deployment"}
        assert azure["configured"] is False

    async def test_shared_from_reports_the_siblings_display_name_not_its_id(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_shared"})
        views = await service.list_views(user.id)
        groq_llm = next(v for v in views if v["service"] == "groq_llm")
        assert groq_llm["sharedFrom"] == "Groq — Whisper large-v3 turbo"

    async def test_also_used_by_names_the_sibling_that_reuses_this_key(self, db_session, make_user):
        user = await make_user()
        views = await _service(db_session).list_views(user.id)
        groq = next(v for v in views if v["service"] == "groq")
        assert "Groq — Llama / Qwen" in groq["alsoUsedBy"]

    async def test_can_verify_is_false_without_a_verify_recipe_even_if_configured(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "azure_openai", {
            "apiKey": "k", "endpoint": "https://x.openai.azure.com", "deployment": "gpt",
        })
        views = await service.list_views(user.id)
        assert next(v for v in views if v["service"] == "azure_openai")["canVerify"] is False

    async def test_can_verify_is_false_when_not_configured_even_with_a_recipe(self, db_session, make_user):
        views = await _service(db_session).list_views("nonexistent-user-id")
        assert next(v for v in views if v["service"] == "groq")["canVerify"] is False

    async def test_hints_never_contain_the_plaintext_secret(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_absolutely_secret_value"})
        views = await service.list_views(user.id)
        hint = next(v for v in views if v["service"] == "groq")["hints"]["apiKey"]
        assert "absolutely_secret_value" not in hint

    async def test_env_configured_credentials_report_a_masked_hint_too(self, db_session, make_user):
        user = await make_user()
        settings = _settings(CEREBRAS_API_KEY="env-cerebras-super-secret")
        views = await _service(db_session, settings).list_views(user.id)
        cerebras = next(v for v in views if v["service"] == "cerebras")
        assert cerebras["source"] == "ENV"
        assert "super-secret" not in cerebras["hints"].get("apiKey", "")


class TestModuleAvailability:
    async def test_a_configured_module_reports_live(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_x"})
        modules = await service.module_availability(user.id)
        transcription = next(m for m in modules if m["module"] == "TRANSCRIPTION")
        assert transcription["state"] == "live"
        assert transcription["activeService"] == "groq"

    async def test_integration_mocks_when_unconfigured_in_mock_mode(self, db_session, make_user):
        user = await make_user()
        settings = _settings(INTEGRATIONS_MODE="mock")
        modules = await _service(db_session, settings).module_availability(user.id)
        assert next(m for m in modules if m["module"] == "INTEGRATION")["state"] == "mocked"

    async def test_a_module_with_a_local_only_entry_is_always_live(self, db_session, make_user):
        # TRANSCRIPTION (local_whisper), EXTRACTION (ollama), TRANSLATION (libretranslate)
        # and EMBEDDING (local_bge) each have a local-only fallback that needs no key at
        # all, so — matching the original exactly — they can never report "unavailable".
        # INTEGRATION is the only module without one.
        user = await make_user()
        modules = await _service(db_session).module_availability(user.id)
        for module in ("TRANSCRIPTION", "EXTRACTION", "TRANSLATION", "EMBEDDING"):
            assert next(m for m in modules if m["module"] == module)["state"] == "live"

    async def test_integration_is_unavailable_with_nothing_configured_outside_mock_mode(
        self, db_session, make_user
    ):
        user = await make_user()
        settings = _settings(INTEGRATIONS_MODE="live")
        modules = await _service(db_session, settings).module_availability(user.id)
        assert next(m for m in modules if m["module"] == "INTEGRATION")["state"] == "unavailable"


class TestSave:
    async def test_saving_without_encryption_configured_is_refused(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session, _settings(APP_ENCRYPTION_KEY=None))
        with pytest.raises(AppError) as exc:
            await service.save(user.id, "groq", {"apiKey": "x"})
        assert exc.value.status_code == 503
        assert exc.value.code == "encryption_unavailable"

    async def test_a_local_only_service_cannot_be_saved(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).save(user.id, "local_whisper", {"apiKey": "x"})
        assert exc.value.code == "local_only"

    async def test_an_unknown_service_is_rejected(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).save(user.id, "not-a-real-service", {"apiKey": "x"})
        assert exc.value.status_code == 404

    async def test_a_multi_field_service_missing_a_required_field_is_refused(self, db_session, make_user):
        # Only `apiKey` provided for a service that also requires `endpoint`/`deployment` —
        # accepting this would silently store an unusable credential.
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).save(user.id, "azure_openai", {"apiKey": "k"})
        assert exc.value.status_code == 422
        assert exc.value.code == "missing_field"

    async def test_a_complete_multi_field_save_succeeds(self, db_session, make_user):
        user = await make_user()
        result = await _service(db_session).save(user.id, "azure_openai", {
            "apiKey": "k", "endpoint": "https://x.openai.azure.com", "deployment": "gpt-4.1",
        })
        assert result["configured"] is True

    async def test_saving_again_resets_verification_status(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_first"})
        with _mocked_http(_FakeResponse(200, "{}"))[0]:
            await service.verify(user.id, "groq")
        result = await service.save(user.id, "groq", {"apiKey": "gsk_second"})
        assert result["status"] == "UNVERIFIED"


class TestDelete:
    async def test_deleting_a_stored_credential_removes_it(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_x"})
        result = await service.delete(user.id, "groq")
        assert result["configured"] is False

    async def test_deleting_a_credential_that_was_never_stored_is_idempotent_not_an_error(
        self, db_session, make_user
    ):
        # groq_llm is satisfied entirely by its sibling and may never have its own row —
        # the original's `deleteMany` succeeds harmlessly here, so this must too.
        user = await make_user()
        result = await _service(db_session).delete(user.id, "groq_llm")
        assert result["configured"] is False

    async def test_an_unknown_service_still_404s(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).delete(user.id, "not-a-real-service")
        assert exc.value.status_code == 404


class TestVerify:
    async def test_a_service_with_no_verify_recipe_is_refused(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "azure_openai", {
            "apiKey": "k", "endpoint": "https://x.openai.azure.com", "deployment": "gpt",
        })
        with pytest.raises(AppError) as exc:
            await service.verify(user.id, "azure_openai")
        assert exc.value.code == "verify_unsupported"

    async def test_verifying_with_no_key_stored_is_refused(self, db_session, make_user):
        user = await make_user()
        with pytest.raises(AppError) as exc:
            await _service(db_session).verify(user.id, "groq")
        assert exc.value.code == "not_configured"

    async def test_a_successful_response_marks_the_key_valid(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_x"})
        mock_ctx, client = _mocked_http(_FakeResponse(200, "{}"))
        with mock_ctx:
            result = await service.verify(user.id, "groq")
        assert result["status"] == "VALID"
        # Bearer auth: the key must reach the provider in the Authorization header.
        _, kwargs = client.request.call_args
        assert kwargs["headers"]["authorization"] == "Bearer gsk_x"

    async def test_slacks_200_with_ok_false_is_still_invalid(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "slack", {"apiKey": "xoxb-fake"})
        with _mocked_http(_FakeResponse(200, '{"ok":false,"error":"invalid_auth"}'))[0]:
            result = await service.verify(user.id, "slack")
        assert result["status"] == "INVALID"
        assert "rejected" in result["lastError"].lower()

    async def test_a_non_2xx_response_is_invalid_with_the_status_reported(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_bad"})
        with _mocked_http(_FakeResponse(401, "unauthorized"))[0]:
            result = await service.verify(user.id, "groq")
        assert result["status"] == "INVALID"
        assert "401" in result["lastError"]

    async def test_a_query_auth_recipe_puts_the_key_in_the_url(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "google_gemini", {"apiKey": "AIzaTest"})
        mock_ctx, client = _mocked_http(_FakeResponse(200, "{}"))
        with mock_ctx:
            await service.verify(user.id, "google_gemini")
        args, _ = client.request.call_args
        assert "key=AIzaTest" in args[1]

    async def test_repeated_verification_is_rate_limited(self, db_session, make_user):
        user = await make_user()
        service = _service(db_session)
        await service.save(user.id, "groq", {"apiKey": "gsk_x"})
        with _mocked_http(_FakeResponse(200, "{}"))[0]:
            await service.verify(user.id, "groq")
            with pytest.raises(AppError) as exc:
                await service.verify(user.id, "groq")
        assert exc.value.status_code == 429

    async def test_an_env_sourced_key_is_verified_without_a_row_to_stamp(self, db_session, make_user):
        user = await make_user()
        settings = _settings(GROQ_API_KEY="env-groq-key")
        service = _service(db_session, settings)
        with _mocked_http(_FakeResponse(200, "{}"))[0]:
            result = await service.verify(user.id, "groq")
        assert result["status"] == "VALID"
        assert result["source"] == "ENV"
