"""Configuration — one validated object, read once, failing loudly.

Mirrors ``src/lib/env.ts``: a missing required key stops the process at import rather
than surfacing as a confusing 500 on the first request that needs it.

The backend and the Next.js frontend now read *different* environment sets. Anything
listed here is the backend's; the frontend keeps only what a browser may see.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # ── core
    DATABASE_URL: str
    APP_ENV: Literal["development", "production", "test"] = "development"
    LOG_LEVEL: Literal["debug", "info", "warn", "error"] = "info"

    # ── auth
    # Signs the JWTs the frontend carries. Distinct from APP_ENCRYPTION_KEY: one signs,
    # the other encrypts, and reusing a key across two purposes is how a signature
    # oracle becomes a decryption oracle.
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = 60 * 30  # 30 minutes
    REFRESH_TOKEN_TTL_SECONDS: int = 60 * 60 * 24 * 14  # 14 days

    # ── encryption at rest for BYOK secrets (SPEC-004)
    APP_ENCRYPTION_KEY: Optional[str] = None
    # When true, the deployment's own keys win over per-user ones — for an organisation
    # that wants a single audited provider account rather than thirty personal ones.
    CREDENTIALS_ENV_LOCKED: bool = False

    # ── CORS: the frontend is now a different origin, so this is load-bearing
    # rather than boilerplate. A wildcard cannot be used with credentials, and is
    # refused below.
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── provider keys (deployment-level fallbacks; a user's own key wins).
    # One entry per src/lib/credentials/catalog.ts `envVar` — the credential vault
    # (SPEC-004) resolves against whichever of these the deployment has set.
    GROQ_API_KEY: Optional[str] = None
    GOOGLE_GEMINI_API_KEY: Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None
    DEEPGRAM_API_KEY: Optional[str] = None
    ASSEMBLYAI_API_KEY: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    TOGETHER_API_KEY: Optional[str] = None
    COHERE_API_KEY: Optional[str] = None
    HUGGINGFACE_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    DEEPSEEK_API_KEY: Optional[str] = None
    XAI_API_KEY: Optional[str] = None
    AZURE_OPENAI_API_KEY: Optional[str] = None
    # Sibling fields for `azure_openai`'s multi-field env fallback — the primary key
    # alone cannot resolve a usable credential without these two.
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_DEPLOYMENT: Optional[str] = None
    DEEPL_API_KEY: Optional[str] = None
    GOOGLE_TRANSLATE_API_KEY: Optional[str] = None
    VOYAGE_API_KEY: Optional[str] = None
    JINA_API_KEY: Optional[str] = None
    # Distinct from NOTION_CLIENT_ID/SLACK_CLIENT_ID below: those are the OAuth app's own
    # credentials (SPEC-002's IntegrationAccount flow); these are the BYOK bot/integration
    # tokens the credential vault's catalog offers, which nothing currently reads back at
    # execution time — the same true-of-the-original quirk this migration preserves rather
    # than silently fixes (SPEC-015 §7).
    NOTION_API_KEY: Optional[str] = None
    SLACK_BOT_TOKEN: Optional[str] = None

    # ── streaming ASR (SPEC-014 §3)
    SPEECH_PROVIDER: Literal["auto", "deepgram", "whisper_live"] = "auto"
    SPEECH_WS_URL: Optional[str] = None

    # ── integrations (SPEC-002 §2) — mock is the default so an unconfigured deployment
    # cannot email, message, or invite anyone. Matches src/lib/env.ts's own default
    # exactly; an earlier version of this file had it backwards (defaulting to "live"),
    # which docker-compose.yml's explicit `INTEGRATIONS_MODE=mock` masked in every local
    # and containerized run — a deployment that forgot to set this variable would not have
    # been protected by that override.
    INTEGRATIONS_MODE: Literal["mock", "live"] = "mock"
    INTEGRATIONS_LIVE: str = ""

    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    NOTION_CLIENT_ID: Optional[str] = None
    NOTION_CLIENT_SECRET: Optional[str] = None
    SLACK_CLIENT_ID: Optional[str] = None
    SLACK_CLIENT_SECRET: Optional[str] = None
    SENDGRID_API_KEY: Optional[str] = None
    SENDGRID_FROM_EMAIL: Optional[str] = None

    # ── where the frontend lives, for OAuth redirects back into the UI
    FRONTEND_URL: str = "http://localhost:3000"

    @field_validator("DATABASE_URL")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """Force the asyncpg driver.

        Compose and Render both hand out ``postgresql://`` URLs, which SQLAlchemy maps
        to psycopg2 — a sync driver that deadlocks under an async engine. Rewriting here
        rather than at every call site means one place gets it right.
        """
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def _strip_libpq_params(cls, value: str) -> str:
        """asyncpg rejects libpq's ``?schema=`` and ``?sslmode=`` query parameters.

        Prisma writes ``?schema=public`` into DATABASE_URL and Render appends
        ``?sslmode=require``. Both are meaningless to asyncpg and raise
        ``TypeError: connect() got an unexpected keyword argument``. Dropping them keeps
        one URL usable by both stacks during the migration.
        """
        if "?" not in value:
            return value
        base, _, query = value.partition("?")
        keep = [
            part
            for part in query.split("&")
            if part and part.split("=")[0] not in {"schema", "sslmode", "pgbouncer", "connection_limit"}
        ]
        return f"{base}?{'&'.join(keep)}" if keep else base

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        if "*" in origins:
            # Browsers refuse `Access-Control-Allow-Origin: *` alongside credentials, so
            # a wildcard here would silently break every authenticated request. Failing
            # at boot is the kinder outcome.
            raise ValueError(
                "CORS_ORIGINS cannot be '*': credentialed requests require explicit origins. "
                "List the frontend origins instead."
            )
        return origins

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    def integration_is_live(self, provider_id: str) -> bool:
        """Per-provider override of the deployment-wide mode (SPEC-002)."""
        if self.INTEGRATIONS_MODE == "live":
            return True
        allowed = {p.strip() for p in self.INTEGRATIONS_LIVE.split(",") if p.strip()}
        return provider_id in allowed


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
