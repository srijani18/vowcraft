"""BYOK credential vault — SPEC-004.

A faithful port of ``src/lib/credentials/service.ts`` and ``catalog.ts``. The resolution
order is the whole contract, so it is stated here rather than left implicit in control flow:

1. **ENV, when locked.** ``CREDENTIALS_ENV_LOCKED`` lets a deployment forbid per-user keys
   outright — for an organisation that wants one audited account, not thirty personal ones.
2. **The user's own key**, decrypted from the vault.
3. **The deployment's environment key**, as a fallback.
4. **A sibling service's key**, when one account covers several capabilities.

Step 4 exists because of a real support problem: one Groq account serves both transcription
and extraction, but they are separate catalogue entries. Someone who entered their key under
"Transcription" was told "no extraction provider is configured" with no hint that the key
they had just added would work.

An undecryptable row falls through to the environment rather than failing the request. That
is the right call for a rotated ``APP_ENCRYPTION_KEY``: the user gets a working service and
a log line, instead of a dead feature and no explanation.

`notion`, `slack`, and `google` (the OAuth app) are catalogue entries here purely because the
original is: their credential-vault "keys" are never actually read back by the execution
adapters, which use a separate OAuth `IntegrationAccount` flow instead (only `sendgrid` among
the INTEGRATION-module entries is genuinely consulted at runtime). That is a pre-existing
quirk of the app being ported, not something this migration introduces or corrects — the
credentials *page* must look and behave identically either way.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from voice2brd_db import Credential, now_ms

from app.core.config import Settings
from app.core.crypto import DecryptionFailed, decrypt_json, encrypt_json
from app.core.exceptions import AppError, bad_request, not_found, service_unavailable, unprocessable
from app.core.logging import logger
from app.db.repositories.users import AuditRepository

CredentialSource = Literal["USER", "ENV", "NONE"]
PricingTier = Literal["free", "freemium", "paid", "local"]


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    placeholder: str
    secret: bool = True
    required: bool = True
    help: Optional[str] = None


@dataclass(frozen=True)
class VerifyRecipe:
    """The catalogue's cheapest authenticated read for a provider."""

    url: str
    auth: Literal["bearer", "header", "query"]
    method: str = "GET"
    #: Names the header for `auth="header"`.
    header: Optional[str] = None
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceSpec:
    """One catalogue entry. Data, not scattered conditionals (SPEC-004 §6)."""

    service: str
    display_name: str
    module: str
    blurb: str
    tier: PricingTier
    #: The concrete allowance, e.g. "14,400 requests/day".
    cost_note: str
    fields: tuple[FieldSpec, ...] = ()
    env_var: Optional[str] = None
    docs_url: str = ""
    verify: Optional[VerifyRecipe] = None
    #: True when the service needs no key at all (a local model).
    local_only: bool = False
    #: Another service whose key this one also uses — see step 4 above.
    shares_key_with: Optional[str] = None
    #: Representative model ids, shown as a hint in the UI.
    models: tuple[str, ...] = ()


def _api_key(placeholder: str, help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key="apiKey", label="API key", placeholder=placeholder, secret=True, required=True, help=help)


def _openai_compatible_verify(base: str) -> VerifyRecipe:
    """Most providers speak the OpenAI wire format, so one recipe covers many."""
    return VerifyRecipe(url=f"{base}/models", auth="bearer")


CATALOG: tuple[ServiceSpec, ...] = (
    # ══════════════════════════════════════════════════════ TRANSCRIPTION ══
    ServiceSpec(
        "groq", "Groq — Whisper large-v3 turbo", "TRANSCRIPTION",
        "Fastest free Whisper available. The recommended default.", "free",
        "Free tier: ~28,800 audio-seconds/day, no card required.",
        fields=(_api_key("gsk_…"),), env_var="GROQ_API_KEY",
        docs_url="https://console.groq.com/keys",
        verify=_openai_compatible_verify("https://api.groq.com/openai/v1"),
        models=("whisper-large-v3-turbo", "whisper-large-v3"),
    ),
    ServiceSpec(
        "local_whisper", "Local Whisper / WhisperX", "TRANSCRIPTION",
        "Runs in the asr container with diarization. No data leaves the machine.", "local",
        "Free forever. Needs ~2 GB RAM for the small model.",
        docs_url="https://github.com/m-bain/whisperX", local_only=True,
        models=("whisper-small", "whisper-large-v3 + pyannote"),
    ),
    ServiceSpec(
        "assemblyai", "AssemblyAI", "TRANSCRIPTION",
        "Diarization, word timings, and summaries in a single call.", "freemium",
        "$50 free credit, then ~$0.12/hour.",
        fields=(_api_key("…"),), env_var="ASSEMBLYAI_API_KEY",
        docs_url="https://www.assemblyai.com/app/account",
        verify=VerifyRecipe("https://api.assemblyai.com/v2/transcript?limit=1", auth="header", header="authorization"),
    ),
    ServiceSpec(
        "deepgram", "Deepgram Nova", "TRANSCRIPTION",
        "Best streaming latency; built-in diarization. Good for live meetings.", "freemium",
        "$200 free credit, then ~$0.26/hour.",
        fields=(_api_key("…"),), env_var="DEEPGRAM_API_KEY",
        docs_url="https://console.deepgram.com",
        verify=VerifyRecipe("https://api.deepgram.com/v1/projects", auth="header", header="Authorization"),
        models=("nova-3", "nova-2"),
    ),
    ServiceSpec(
        "elevenlabs", "ElevenLabs Scribe", "TRANSCRIPTION",
        "Strong accuracy on accented and noisy audio, 99 languages.", "freemium",
        "Free tier available; paid from ~$0.22/hour.",
        fields=(_api_key("sk_…"),), env_var="ELEVENLABS_API_KEY",
        docs_url="https://elevenlabs.io/app/settings/api-keys",
        verify=VerifyRecipe("https://api.elevenlabs.io/v1/user", auth="header", header="xi-api-key"),
    ),
    ServiceSpec(
        "openai_audio", "OpenAI — Whisper / gpt-4o-transcribe", "TRANSCRIPTION",
        "Reliable baseline quality across 50+ languages.", "paid",
        "~$0.36/hour (whisper-1). No free tier.",
        fields=(_api_key("sk-…"),), env_var="OPENAI_API_KEY", shares_key_with="openai",
        docs_url="https://platform.openai.com/api-keys",
        verify=_openai_compatible_verify("https://api.openai.com/v1"),
        models=("whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"),
    ),

    # ═════════════════════════════════════════════════════════ EXTRACTION ══
    ServiceSpec(
        "google_gemini", "Google Gemini", "EXTRACTION",
        "Generous free tier with reliable function calling. Strong default.", "free",
        "Free tier: 1,500 requests/day on Flash models.",
        fields=(_api_key("AIza…"),), env_var="GOOGLE_GEMINI_API_KEY",
        docs_url="https://aistudio.google.com/apikey",
        verify=VerifyRecipe("https://generativelanguage.googleapis.com/v1beta/models", auth="query"),
        models=("gemini-2.0-flash", "gemini-2.5-pro"),
    ),
    ServiceSpec(
        "groq_llm", "Groq — Llama / Qwen", "EXTRACTION",
        "Sub-second extraction on open models. Uses the same key as Groq transcription.", "free",
        "Free tier: 14,400 requests/day.",
        fields=(_api_key("gsk_…"),), env_var="GROQ_API_KEY", shares_key_with="groq",
        docs_url="https://console.groq.com/keys",
        verify=_openai_compatible_verify("https://api.groq.com/openai/v1"),
        models=("openai/gpt-oss-120b", "llama-3.3-70b-versatile"),
    ),
    ServiceSpec(
        "cerebras", "Cerebras", "EXTRACTION",
        "The fastest open-model inference; free developer tier.", "free",
        "Free tier: 1M tokens/day.",
        fields=(_api_key("csk-…"),), env_var="CEREBRAS_API_KEY",
        docs_url="https://cloud.cerebras.ai",
        verify=_openai_compatible_verify("https://api.cerebras.ai/v1"),
        models=("llama-3.3-70b", "qwen-3-32b"),
    ),
    ServiceSpec(
        "openrouter", "OpenRouter", "EXTRACTION",
        "One key, 300+ models, including several genuinely free ones.", "freemium",
        "Free models available (`:free` suffix); paid models at cost.",
        fields=(_api_key("sk-or-…"),), env_var="OPENROUTER_API_KEY",
        docs_url="https://openrouter.ai/keys",
        verify=VerifyRecipe("https://openrouter.ai/api/v1/key", auth="bearer"),
        models=("meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-r1:free"),
    ),
    ServiceSpec(
        "mistral", "Mistral AI", "EXTRACTION",
        "European hosting, solid structured output, free experiment tier.", "freemium",
        "Free experimentation tier; paid from ~$0.2/M tokens.",
        fields=(_api_key("…"),), env_var="MISTRAL_API_KEY",
        docs_url="https://console.mistral.ai/api-keys",
        verify=_openai_compatible_verify("https://api.mistral.ai/v1"),
        models=("mistral-large-latest", "mistral-small-latest"),
    ),
    ServiceSpec(
        "together", "Together AI", "EXTRACTION",
        "Broad open-model catalogue with a free starter credit.", "freemium",
        "$1 free credit; some models free at low rate limits.",
        fields=(_api_key("…"),), env_var="TOGETHER_API_KEY",
        docs_url="https://api.together.xyz/settings/api-keys",
        verify=_openai_compatible_verify("https://api.together.xyz/v1"),
    ),
    ServiceSpec(
        "cohere", "Cohere", "EXTRACTION",
        "Command models with a free trial key for non-production use.", "freemium",
        "Free trial key: 1,000 calls/month, rate limited.",
        fields=(_api_key("…"),), env_var="COHERE_API_KEY",
        docs_url="https://dashboard.cohere.com/api-keys",
        verify=VerifyRecipe("https://api.cohere.com/v1/models", auth="bearer"),
    ),
    ServiceSpec(
        "huggingface", "Hugging Face Inference", "EXTRACTION",
        "Serverless inference over thousands of open models.", "freemium",
        "Free monthly credits on the serverless API.",
        fields=(_api_key("hf_…"),), env_var="HUGGINGFACE_API_KEY",
        docs_url="https://huggingface.co/settings/tokens",
        verify=VerifyRecipe("https://huggingface.co/api/whoami-v2", auth="bearer"),
    ),
    ServiceSpec(
        "ollama", "Ollama (local)", "EXTRACTION",
        "Runs models on your own hardware. Nothing leaves the machine.", "local",
        "Free. Set OLLAMA_BASE_URL if it is not on localhost:11434.",
        docs_url="https://ollama.com", local_only=True,
        models=("llama3.3", "qwen2.5", "mistral-nemo"),
    ),
    ServiceSpec(
        "anthropic", "Anthropic — Claude", "EXTRACTION",
        "Strongest at structured extraction and following guardrail instructions.", "paid",
        "Pay per token; no free tier. Cheapest via Haiku.",
        fields=(_api_key("sk-ant-…"),), env_var="ANTHROPIC_API_KEY",
        docs_url="https://console.anthropic.com/settings/keys",
        verify=VerifyRecipe(
            "https://api.anthropic.com/v1/models", auth="header", header="x-api-key",
            extra_headers={"anthropic-version": "2023-06-01"},
        ),
        models=("claude-sonnet-4-5", "claude-haiku-4-5"),
    ),
    ServiceSpec(
        "openai", "OpenAI — GPT", "EXTRACTION",
        "Mature function calling and strict JSON schema support.", "paid",
        "Pay per token; no free tier.",
        fields=(_api_key("sk-…"),), env_var="OPENAI_API_KEY",
        docs_url="https://platform.openai.com/api-keys",
        verify=_openai_compatible_verify("https://api.openai.com/v1"),
        models=("gpt-4.1", "gpt-4.1-mini"),
    ),
    ServiceSpec(
        "deepseek", "DeepSeek", "EXTRACTION",
        "Very low cost per token for high-volume batch extraction.", "paid",
        "Paid, but roughly an order of magnitude cheaper than peers.",
        fields=(_api_key("sk-…"),), env_var="DEEPSEEK_API_KEY",
        docs_url="https://platform.deepseek.com/api_keys",
        verify=_openai_compatible_verify("https://api.deepseek.com/v1"),
    ),
    ServiceSpec(
        "xai", "xAI — Grok", "EXTRACTION",
        "OpenAI-compatible API with a large context window.", "paid",
        "Pay per token; promotional credits appear periodically.",
        fields=(_api_key("xai-…"),), env_var="XAI_API_KEY",
        docs_url="https://console.x.ai",
        verify=_openai_compatible_verify("https://api.x.ai/v1"),
    ),
    ServiceSpec(
        "azure_openai", "Azure OpenAI", "EXTRACTION",
        "For organisations that require an Azure-resident deployment.", "paid",
        "Azure billing. Needs the endpoint as well as the key.",
        fields=(
            _api_key("…"),
            FieldSpec("endpoint", "Endpoint", "https://my-resource.openai.azure.com", secret=False, required=True),
            FieldSpec("deployment", "Deployment name", "gpt-4.1", secret=False, required=True),
        ),
        env_var="AZURE_OPENAI_API_KEY",
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
    ),

    # ════════════════════════════════════════════════════════ TRANSLATION ══
    ServiceSpec(
        "deepl", "DeepL", "TRANSLATION",
        "Highest translation quality for European languages.", "free",
        "Free tier: 500,000 characters/month.",
        fields=(_api_key("…:fx"),), env_var="DEEPL_API_KEY",
        docs_url="https://www.deepl.com/pro-api",
        verify=VerifyRecipe("https://api-free.deepl.com/v2/usage", auth="header", header="Authorization"),
    ),
    ServiceSpec(
        "libretranslate", "LibreTranslate (self-hosted)", "TRANSLATION",
        "Open-source translation you can run beside the app.", "local",
        "Free. Set LIBRETRANSLATE_URL to your instance.",
        docs_url="https://libretranslate.com", local_only=True,
    ),
    ServiceSpec(
        "google_translate", "Google Cloud Translation", "TRANSLATION",
        "Widest language coverage, including low-resource languages.", "freemium",
        "First 500k characters/month free, then ~$20/M.",
        fields=(_api_key("AIza…"),), env_var="GOOGLE_TRANSLATE_API_KEY",
        docs_url="https://cloud.google.com/translate/docs/setup",
    ),

    # ═════════════════════════════════════════════ EMBEDDING / SEARCH ══
    ServiceSpec(
        "voyage", "Voyage AI", "EMBEDDING",
        "Retrieval-tuned embeddings; the best free option for search quality.", "free",
        "Free tier: 200M tokens.",
        fields=(_api_key("pa-…"),), env_var="VOYAGE_API_KEY",
        docs_url="https://dash.voyageai.com",
        models=("voyage-3", "voyage-3-lite"),
    ),
    ServiceSpec(
        "jina", "Jina AI", "EMBEDDING",
        "Multilingual embeddings with a no-signup trial key.", "free",
        "Free tier: 1M tokens, no card required.",
        fields=(_api_key("jina_…"),), env_var="JINA_API_KEY",
        docs_url="https://jina.ai/embeddings",
    ),
    ServiceSpec(
        "local_bge", "Local BGE / e5 (asr container)", "EMBEDDING",
        "Sentence-transformers running locally. Keeps transcripts private.", "local",
        "Free. Adds ~500 MB to the asr image.",
        docs_url="https://huggingface.co/BAAI/bge-m3", local_only=True,
    ),
    ServiceSpec(
        "openai_embeddings", "OpenAI embeddings", "EMBEDDING",
        "text-embedding-3, the common baseline for pgvector setups.", "paid",
        "~$0.02/M tokens for the small model.",
        fields=(_api_key("sk-…"),), env_var="OPENAI_API_KEY", shares_key_with="openai",
        docs_url="https://platform.openai.com/api-keys",
        verify=_openai_compatible_verify("https://api.openai.com/v1"),
        models=("text-embedding-3-small", "text-embedding-3-large"),
    ),

    # ═══════════════════════════════════════════════════════ INTEGRATIONS ══
    ServiceSpec(
        "notion", "Notion", "INTEGRATION",
        "Internal integration token for creating task pages.", "free",
        "Free with any Notion plan.",
        fields=(_api_key("ntn_…", "Create an internal integration, then share your task database with it."),),
        env_var="NOTION_API_KEY",
        docs_url="https://www.notion.so/my-integrations",
        verify=VerifyRecipe(
            "https://api.notion.com/v1/users/me", auth="bearer",
            extra_headers={"Notion-Version": "2022-06-28"},
        ),
    ),
    ServiceSpec(
        "slack", "Slack", "INTEGRATION",
        "Bot token for scheduled reminders and channel messages.", "free",
        "Free with any Slack workspace.",
        fields=(_api_key("xoxb-…"),), env_var="SLACK_BOT_TOKEN",
        docs_url="https://api.slack.com/apps",
        verify=VerifyRecipe("https://slack.com/api/auth.test", auth="bearer"),
    ),
    ServiceSpec(
        "google", "Google OAuth app (Calendar + Gmail)", "INTEGRATION",
        "Client id and secret for the OAuth app. Each user then grants consent separately.", "free",
        "Free. Calendar and Gmail APIs have no per-call charge.",
        fields=(
            FieldSpec("clientId", "Client ID", "…apps.googleusercontent.com", secret=False, required=True),
            FieldSpec("clientSecret", "Client secret", "GOCSPX-…", secret=True, required=True),
        ),
        env_var="GOOGLE_CLIENT_ID",
        docs_url="https://console.cloud.google.com/apis/credentials",
    ),
    ServiceSpec(
        "sendgrid", "SendGrid", "INTEGRATION",
        "Sends as the organisation from a verified domain — no per-user consent, works "
        "unattended. Cannot save drafts; Gmail handles those.", "freemium",
        "Free tier: 100 emails/day. Also set SENDGRID_FROM_EMAIL to a verified sender.",
        fields=(_api_key("SG.…", "Needs at least the mail.send scope."),), env_var="SENDGRID_API_KEY",
        docs_url="https://app.sendgrid.com/settings/api_keys",
        verify=VerifyRecipe("https://api.sendgrid.com/v3/scopes", auth="bearer"),
    ),
)

_BY_SERVICE = {spec.service: spec for spec in CATALOG}

#: Free and local first, then freemium, then paid — the order the UI renders (kept here only
#: because `module_availability` needs the *first resolvable* entry per module; the frontend
#: does its own presentation-layer sort from `src/lib/credentials/catalog.ts` directly).
_TIER_RANK: dict[str, int] = {"free": 0, "local": 1, "freemium": 2, "paid": 3}

MODULE_ORDER: tuple[str, ...] = ("TRANSCRIPTION", "EXTRACTION", "TRANSLATION", "EMBEDDING", "INTEGRATION")

MODULE_META: dict[str, dict[str, str]] = {
    "TRANSCRIPTION": {
        "title": "Transcription", "icon": "bi-soundwave",
        "blurb": "Speech to text with word timings and speaker labels.",
    },
    "EXTRACTION": {
        "title": "Action extraction", "icon": "bi-diagram-3",
        "blurb": "Turns a transcript into structured, owned, dated action items.",
    },
    "TRANSLATION": {
        "title": "Translation", "icon": "bi-translate",
        "blurb": "Reads a transcript back in another language.",
    },
    "EMBEDDING": {
        "title": "Semantic search", "icon": "bi-search",
        "blurb": "Embeddings for meaning-based transcript search.",
    },
    "INTEGRATION": {
        "title": "Integrations", "icon": "bi-plug",
        "blurb": "Where approved actions are actually executed.",
    },
}


def find_service(service: str) -> Optional[ServiceSpec]:
    return _BY_SERVICE.get(service)


def _siblings_of(service: str) -> list[ServiceSpec]:
    """Services that share a key with `service`, in either direction — so the UI can say
    "also used for action extraction" on the entry the key was actually entered against."""
    spec = find_service(service)
    primary = spec.shares_key_with if (spec and spec.shares_key_with) else service
    return [s for s in CATALOG if s.service != service and (s.service == primary or s.shares_key_with == primary)]


def _primary_field(spec: ServiceSpec) -> Optional[FieldSpec]:
    return next((f for f in spec.fields if f.required), spec.fields[0] if spec.fields else None)


@dataclass
class ResolvedCredential:
    service: str
    source: CredentialSource
    secrets: dict[str, str] = field(default_factory=dict)
    #: Set when the key came from a sibling entry, so a caller can say so honestly.
    shared_from: Optional[str] = None

    @property
    def api_key(self) -> Optional[str]:
        return self.secrets.get("apiKey")

    @property
    def configured(self) -> bool:
        return self.source != "NONE" and bool(self.secrets)


class CredentialService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _env_value(self, spec: ServiceSpec) -> Optional[str]:
        if not spec.env_var:
            return None
        value = getattr(self.settings, spec.env_var, None)
        return value.strip() if value and str(value).strip() else None

    def _env_secrets(self, spec: ServiceSpec, primary_value: str) -> dict[str, str]:
        """Maps a single env var onto the spec's field shape. A multi-field service reads
        its remaining fields from sibling env vars, e.g. `AZURE_OPENAI_ENDPOINT`."""
        primary = _primary_field(spec)
        if primary is None:
            return {}
        secrets = {primary.key: primary_value}
        for f in spec.fields:
            if f.key == primary.key:
                continue
            # `clientId` -> `CLIENT_ID`, matching src/lib/credentials/service.ts's envSecrets.
            snake_key = re.sub(r"([A-Z])", r"_\1", f.key).upper()
            sibling_var = f"{spec.service.upper()}_{snake_key}"
            sibling_value = getattr(self.settings, sibling_var, None)
            if sibling_value and str(sibling_value).strip():
                secrets[f.key] = str(sibling_value).strip()
        return secrets

    async def _row(self, user_id: str, service: str) -> Optional[Credential]:
        return await self.session.scalar(
            select(Credential).where(Credential.user_id == user_id, Credential.service == service)
        )

    def _decrypt(self, row: Credential, user_id: str, service: str) -> Optional[dict[str, str]]:
        try:
            secrets = decrypt_json(row.secrets_enc, self.settings.APP_ENCRYPTION_KEY, f"cred:{user_id}:{service}")
            return {k: str(v) for k, v in secrets.items()} if isinstance(secrets, dict) and secrets else None
        except DecryptionFailed as exc:
            logger.error("credential.decrypt_failed", service=service, userId=user_id, err=exc)
            return None

    async def resolve(
        self, user_id: str, service: str, _seen: Optional[frozenset[str]] = None
    ) -> ResolvedCredential:
        seen = _seen or frozenset()
        spec = find_service(service)
        if spec is None:
            return ResolvedCredential(service=service, source="NONE")

        if spec.local_only:
            return ResolvedCredential(service=service, source="ENV", secrets={})

        from_env = self._env_value(spec)

        if self.settings.CREDENTIALS_ENV_LOCKED and from_env:
            return ResolvedCredential(service, "ENV", self._env_secrets(spec, from_env))

        row = await self._row(user_id, service)
        if row is not None and row.enabled and row.status != "INVALID":
            secrets = self._decrypt(row, user_id, service)
            if secrets:
                return ResolvedCredential(service, "USER", secrets)

        if from_env:
            return ResolvedCredential(service, "ENV", self._env_secrets(spec, from_env))

        # Fall through to the service this one shares a key with. `seen` guards against a
        # mis-authored catalogue pointing two entries at each other.
        if spec.shares_key_with and spec.shares_key_with not in seen:
            shared = await self.resolve(user_id, spec.shares_key_with, seen | {service})
            if shared.configured:
                # Reported under the service that was asked for, so callers and logs stay
                # meaningful, but the secrets came from the sibling.
                return ResolvedCredential(service, shared.source, shared.secrets)

        return ResolvedCredential(service, "NONE")

    async def resolve_module(self, user_id: str, module: str) -> Optional[ResolvedCredential]:
        """First service in a module that resolves to a usable key."""
        for spec in (s for s in CATALOG if s.module == module):
            resolved = await self.resolve(user_id, spec.service)
            if resolved.source != "NONE" or spec.local_only:
                return resolved
        return None

    # ────────────────────────────────────────────────────────── read API ──

    async def list_views(self, user_id: str) -> list[dict[str, Any]]:
        rows = {
            r.service: r
            for r in (await self.session.scalars(select(Credential).where(Credential.user_id == user_id))).all()
        }
        env_locked = self.settings.CREDENTIALS_ENV_LOCKED

        def satisfied_by(spec: ServiceSpec) -> Optional[tuple[str, CredentialSource]]:
            """A service is also satisfied by whatever its shared sibling has."""
            if not spec.shares_key_with:
                return None
            sibling = find_service(spec.shares_key_with)
            if sibling is None:
                return None
            sibling_row = rows.get(sibling.service)
            if sibling_row is not None and sibling_row.enabled and sibling_row.status != "INVALID":
                if self._decrypt(sibling_row, user_id, sibling.service):
                    return sibling.display_name, "USER"
            sibling_env = self._env_value(sibling)
            return (sibling.display_name, "ENV") if sibling_env else None

        views: list[dict[str, Any]] = []
        for spec in CATALOG:
            row = rows.get(spec.service)
            from_env = self._env_value(spec)
            shared = satisfied_by(spec)

            # A stored row only counts as usable if it actually decrypts. Reporting
            # "configured" for a row we cannot read would be a lie the UI acts on.
            user_usable = False
            decrypt_error: Optional[str] = None
            if row is not None and row.enabled and row.status != "INVALID":
                if self._decrypt(row, user_id, spec.service):
                    user_usable = True
                else:
                    decrypt_error = (
                        "The stored key could not be decrypted. APP_ENCRYPTION_KEY may have "
                        "changed — re-enter the key."
                    )

            source: CredentialSource
            if spec.local_only:
                source = "ENV"
            elif env_locked and from_env:
                source = "ENV"
            elif user_usable:
                source = "USER"
            elif from_env:
                source = "ENV"
            else:
                source = shared[1] if shared else "NONE"

            if user_usable:
                hints = dict(row.hints) if row is not None and row.hints else {}
            elif from_env:
                primary = _primary_field(spec)
                hints = {primary.key: _mask(from_env)} if primary else {}
            else:
                hints = {}

            views.append(
                {
                    "service": spec.service,
                    "displayName": spec.display_name,
                    "module": spec.module,
                    "blurb": spec.blurb,
                    "docsUrl": spec.docs_url,
                    "tier": spec.tier,
                    "costNote": spec.cost_note,
                    "models": list(spec.models),
                    "localOnly": spec.local_only,
                    "source": source,
                    "configured": source != "NONE",
                    "enabled": row.enabled if row is not None else True,
                    "status": "INVALID" if decrypt_error else (row.status if row is not None else "UNVERIFIED"),
                    "lastVerifiedAt": (
                        row.last_verified_at.isoformat() + "Z"
                        if row is not None and row.last_verified_at
                        else None
                    ),
                    "lastError": decrypt_error or (row.last_error if row is not None else None),
                    "hints": hints,
                    "fields": [
                        {
                            "key": f.key, "label": f.label, "placeholder": f.placeholder,
                            "secret": f.secret, "required": f.required, "help": f.help,
                        }
                        for f in spec.fields
                    ],
                    "canVerify": spec.verify is not None and source != "NONE",
                    # Set when this entry needs no key of its own because a sibling supplied one.
                    "sharedFrom": shared[0] if (not user_usable and not from_env and shared) else None,
                    # Where this key is *also* used, so the UI can say so at the point of entry.
                    "alsoUsedBy": [
                        s.display_name for s in _siblings_of(spec.service) if s.shares_key_with == spec.service
                    ],
                }
            )
        return views

    async def module_availability(self, user_id: str) -> list[dict[str, Any]]:
        """Drives the persistent availability banner — SPEC-004 §7."""
        views = await self.list_views(user_id)
        result = []
        for module in MODULE_ORDER:
            meta = MODULE_META[module]
            configured = next((v for v in views if v["module"] == module and v["configured"]), None)
            mock_covers = module == "INTEGRATION" and self.settings.INTEGRATIONS_MODE == "mock"
            result.append(
                {
                    "module": module,
                    "title": meta["title"],
                    "blurb": meta["blurb"],
                    "icon": meta["icon"],
                    "state": "live" if configured else ("mocked" if mock_covers else "unavailable"),
                    "activeService": configured["service"] if configured else None,
                    "source": configured["source"] if configured else "NONE",
                }
            )
        return result

    # ───────────────────────────────────────────────────────── write API ──

    async def save(
        self, user_id: str, service: str, secrets: dict[str, str], *,
        label: Optional[str] = None, enabled: Optional[bool] = None, request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        spec = find_service(service)
        if spec is None:
            raise not_found(f"Unknown service “{service}”.")
        if spec.local_only:
            raise bad_request("local_only", f"{spec.display_name} needs no API key.")
        if not self.settings.APP_ENCRYPTION_KEY:
            raise service_unavailable(
                "encryption_unavailable",
                "APP_ENCRYPTION_KEY is not configured, so secrets cannot be stored safely. "
                "Generate one with: openssl rand -base64 32",
            )

        cleaned: dict[str, str] = {}
        for f in spec.fields:
            value = secrets.get(f.key)
            if isinstance(value, str) and value.strip():
                cleaned[f.key] = value.strip()
            elif f.required:
                raise unprocessable("missing_field", f"{f.label} is required for {spec.display_name}.", {"field": f.key})

        hints = {k: _mask(v) for k, v in cleaned.items()}
        sealed = encrypt_json(cleaned, self.settings.APP_ENCRYPTION_KEY, f"cred:{user_id}:{service}")

        existing = await self._row(user_id, service)
        if existing is None:
            self.session.add(
                Credential(
                    user_id=user_id, module=spec.module, service=service, label=label,
                    secrets_enc=sealed, hints=hints, enabled=enabled if enabled is not None else True,
                    status="UNVERIFIED",
                )
            )
        else:
            existing.secrets_enc = sealed
            existing.hints = hints
            existing.label = label
            if enabled is not None:
                existing.enabled = enabled
            # A new value invalidates the previous verification result.
            existing.status = "UNVERIFIED"
            existing.last_error = None
            existing.last_verified_at = None
        await self.session.flush()

        await AuditRepository(self.session).record(
            event="credential.saved", actor_id=user_id, request_id=request_id,
            # Field names only — never a value (SPEC-004 §9).
            metadata={"service": service, "module": spec.module, "fields": list(cleaned.keys())},
        )

        views = await self.list_views(user_id)
        return next(v for v in views if v["service"] == service)

    async def delete(self, user_id: str, service: str, request_id: Optional[str] = None) -> dict[str, Any]:
        spec = find_service(service)
        if spec is None:
            raise not_found(f"Unknown service “{service}”.")

        # Idempotent by design, matching the original: deleting a credential that was
        # never stored (e.g. one only ever satisfied by a sibling's key) is not an error,
        # it just leaves the recomputed view exactly as it already was.
        existing = await self._row(user_id, service)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

        await AuditRepository(self.session).record(
            event="credential.deleted", actor_id=user_id, request_id=request_id,
            metadata={"service": service},
        )

        views = await self.list_views(user_id)
        return next(v for v in views if v["service"] == service)

    # ───────────────────────────────────────────────────── verification ──

    _last_verify: dict[str, float] = {}
    _VERIFY_COOLDOWN_S = 10.0

    async def verify(self, user_id: str, service: str, request_id: Optional[str] = None) -> dict[str, Any]:
        """SPEC-004 §8. Performs the catalogue's cheapest authenticated read."""
        spec = find_service(service)
        if spec is None:
            raise not_found(f"Unknown service “{service}”.")
        if spec.verify is None:
            raise bad_request("verify_unsupported", f"{spec.display_name} has no verification endpoint.")

        throttle_key = f"{user_id}:{service}"
        last = self._last_verify.get(throttle_key)
        # `time.monotonic()`'s reference point is undefined by the stdlib — it can start
        # near zero — so "never verified" must be tracked explicitly, not assumed via a
        # 0.0 sentinel that a fresh process could already be within the cooldown of.
        if last is not None and time.monotonic() - last < self._VERIFY_COOLDOWN_S:
            raise AppError(429, "too_many_requests", "Wait a few seconds before checking this key again.")
        self._last_verify[throttle_key] = time.monotonic()

        resolved = await self.resolve(user_id, service)
        primary = _primary_field(spec)
        key = resolved.secrets.get(primary.key) if primary else None
        if not key:
            raise unprocessable("not_configured", f"No key stored for {spec.display_name}.")

        recipe = spec.verify
        url = recipe.url
        headers = dict(recipe.extra_headers)
        if recipe.auth == "bearer":
            headers["authorization"] = f"Bearer {key}"
        elif recipe.auth == "header":
            headers[recipe.header or "authorization"] = key
        else:
            url += f"{'&' if '?' in url else '?'}key={key}"

        status = "INVALID"
        error: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.request(recipe.method, url, headers=headers)
            if response.is_success:
                # Slack answers 200 with ok:false for a bad token, so a 2xx is not enough.
                if re.search(r'"ok"\s*:\s*false', response.text):
                    status = "INVALID"
                    error = "The provider rejected this token."
                else:
                    status = "VALID"
            else:
                error = f"{response.status_code} {response.reason_phrase}".strip()
        except httpx.TimeoutException:
            error = "The provider did not respond in time."
        except httpx.HTTPError as exc:
            error = str(exc)

        # Only a user-stored key has a row to stamp; an env key is the operator's.
        if resolved.source == "USER":
            existing = await self._row(user_id, service)
            if existing is not None:
                existing.status = status
                existing.last_error = error
                existing.last_verified_at = now_ms()
                await self.session.flush()

        await AuditRepository(self.session).record(
            event="credential.verified", actor_id=user_id, request_id=request_id,
            metadata={"service": service, "status": status, "source": resolved.source, "error": error},
        )

        views = await self.list_views(user_id)
        view = next(v for v in views if v["service"] == service)
        # Env-sourced keys have no row, so surface this run's result directly.
        if resolved.source != "USER":
            view = {**view, "status": status, "lastError": error}
        return view


def _mask(value: str) -> str:
    """A masked preview: enough to recognise the key, never enough to use it."""
    if len(value) <= 8:
        return "…" * 4
    return f"{value[:4]}…{value[-4:]}"
