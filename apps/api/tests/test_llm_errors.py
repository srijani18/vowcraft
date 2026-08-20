"""Provider failure classification — SPEC-010 §3.4.

The behaviour under test is a separation: the provider's words go to the operator, a written
sentence goes to the user, and neither borrows from the other.
"""

from __future__ import annotations

import json

import pytest

from app.services.llm import (
    PROVIDERS,
    ExtractionError,
    _http_error,
    is_model_unavailable,
    model_unavailable_message,
)

GROQ = PROVIDERS[0]

MODEL_NOT_FOUND = json.dumps({
    "error": {
        "message": "The model `openai/gpt-oss-120b-typo` does not exist or you do not have access to it.",
        "type": "invalid_request_error", "code": "model_not_found",
    }
})
MODEL_DECOMMISSIONED = json.dumps({
    "error": {
        "message": "The model `llama-3.1-70b-versatile` has been decommissioned. Please refer to "
                   "https://console.groq.com/docs/deprecations",
        "type": "invalid_request_error", "code": "model_decommissioned",
    }
})


class TestConfiguredModel:
    def test_groq_uses_openai_gpt_oss_120b(self):
        assert GROQ.model == "openai/gpt-oss-120b"

    def test_brand_names_the_vendor_not_the_model(self):
        """Error text should point at the configuration the user can change, and
        display_name embeds the very model being reported wrong."""
        assert GROQ.brand == "Groq"
        assert GROQ.model not in GROQ.brand


class TestClassification:
    @pytest.mark.parametrize("status,body,expected", [
        (404, "", True),
        (404, "<html>404 Not Found</html>", True),
        (400, MODEL_DECOMMISSIONED, True),
        (400, '{"error":{"code":"model_not_found"}}', True),
        (400, '{"error":{"code":"context_length_exceeded"}}', False),
        (429, '{"error":{"code":"rate_limit_exceeded"}}', False),
        (401, '{"error":{"code":"invalid_api_key"}}', False),
    ])
    def test_by_status_or_code_never_by_provider(self, status, body, expected):
        assert is_model_unavailable(status, body) is expected

    def test_the_sentence_names_whichever_vendor_was_configured(self):
        assert model_unavailable_message("Gemini") == (
            "The extraction model is unavailable. Please check the configured Gemini model and "
            "try again."
        )


class TestErrorSplit:
    def test_a_404_produces_the_exact_specified_sentence(self):
        err = _http_error(GROQ, 404, MODEL_NOT_FOUND)
        assert err.code == "model_unavailable"
        assert err.message == (
            "The extraction model is unavailable. Please check the configured Groq model and "
            "try again."
        )

    @pytest.mark.parametrize("leak", ["model_not_found", "invalid_request_error",
                                      "do not have access", "404", "{"])
    def test_no_part_of_the_response_reaches_the_user(self, leak):
        assert leak not in _http_error(GROQ, 404, MODEL_NOT_FOUND).message

    def test_the_body_is_kept_for_the_operator(self):
        err = _http_error(GROQ, 404, MODEL_NOT_FOUND)
        assert err.detail and "model_not_found" in err.detail

    def test_the_attempted_model_is_recorded(self):
        """A 404 is only actionable if you can see which id produced it."""
        assert _http_error(GROQ, 404, MODEL_NOT_FOUND).model == "openai/gpt-oss-120b"

    def test_a_bad_model_is_not_auto_retryable(self):
        """The identical request returns the identical 404. The user retries after changing
        the configuration, which is a different request."""
        assert _http_error(GROQ, 404, MODEL_NOT_FOUND).retryable is False

    def test_a_decommissioned_model_reads_the_same_to_the_user(self):
        err = _http_error(GROQ, 400, MODEL_DECOMMISSIONED)
        assert err.code == "model_unavailable"
        # The decommission notice, including its docs link, is kept where an operator sees it.
        assert err.detail and "deprecations" in err.detail
        assert "deprecations" not in err.message

    def test_401_points_at_the_key_and_does_not_classify_as_a_model_problem(self):
        err = _http_error(GROQ, 401, '{"error":{"code":"invalid_api_key"}}')
        assert err.code != "model_unavailable"
        assert "rejected the API key" in err.message
        assert "invalid_api_key" not in err.message

    def test_a_500_body_is_not_echoed_but_is_kept(self):
        body = '{"error":{"message":"upstream pod OOM at 10.4.2.19:8080","internal_trace":"abc"}}'
        err = _http_error(GROQ, 500, body)
        assert "10.4.2.19" not in err.message
        assert "internal_trace" not in err.message
        assert err.detail and "internal_trace" in err.detail
        # Transient, unlike a bad model id.
        assert err.retryable is True

    def test_429_stays_retryable_and_mentions_the_free_tier_resetting(self):
        err = _http_error(GROQ, 429, "{}")
        assert err.retryable is True
        assert "rate limit" in err.message.lower()
