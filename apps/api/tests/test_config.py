"""Safety-relevant config defaults — SPEC-002 §2.

Not behaviour a fixture-comparison test would catch: these are properties of the
*absence* of configuration, which only a test that deliberately clears the environment
variable can see.
"""

from __future__ import annotations

from app.core.config import Settings


class TestIntegrationsModeDefault:
    def test_defaults_to_mock_when_unset(self, monkeypatch):
        # A real regression, not a hypothetical: this field briefly defaulted to "live",
        # which docker-compose.yml's explicit override masked in every local run. A
        # deployment that forgot to set INTEGRATIONS_MODE would not have had that
        # override, and would have silently reached real providers by default —
        # contradicting "mock is the default so an unconfigured app cannot email anyone"
        # (src/lib/env.ts carries the same default for the same reason).
        monkeypatch.delenv("INTEGRATIONS_MODE", raising=False)
        assert Settings().INTEGRATIONS_MODE == "mock"
