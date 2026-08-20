"""LLM providers — forced single tool call — SPEC-010 §3.

One implementation covers every OpenAI-compatible provider (Groq, Cerebras, OpenAI,
Mistral); Gemini needs its own request shape and gets its own branch. Adding a provider is a
base URL and a model id, not an integration.

**Error handling is the interesting part.** ``message`` is the only field that reaches the
user and it never interpolates a response body; the body goes to ``detail``, which is logged
and dropped. That split exists because providers put request echoes, internal ids and
occasionally the tail of a credential into error bodies (SPEC-010 §3.4).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import Settings
from app.core.logging import Logger, logger
from app.services.credentials import CredentialService

_TIMEOUT_SECONDS = 180


class ExtractionError(Exception):
    """A provider failure, split for two audiences."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        detail: Optional[str] = None,
        status: Optional[int] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        #: The provider's own words. Logged; never rendered.
        self.detail = detail
        self.status = status
        self.model = model


@dataclass(frozen=True)
class LlmProvider:
    id: str
    display_name: str
    #: The vendor without the model in it — error text should point at the configuration
    #: the user can change, and `display_name` embeds the very model being reported wrong.
    brand: str
    model: str
    credential_service: str
    free_tier: bool
    base_url: Optional[str] = None
    kind: str = "openai"  # "openai" | "gemini"


PROVIDERS: tuple[LlmProvider, ...] = (
    LlmProvider(
        "groq_llm", "Groq (GPT-OSS 120B)", "Groq", "openai/gpt-oss-120b", "groq_llm", True,
        "https://api.groq.com/openai/v1",
    ),
    LlmProvider(
        "google_gemini", "Google Gemini", "Gemini", "gemini-2.0-flash", "google_gemini", True,
        kind="gemini",
    ),
    LlmProvider(
        "cerebras", "Cerebras (Llama 3.3 70B)", "Cerebras", "llama-3.3-70b", "cerebras", True,
        "https://api.cerebras.ai/v1",
    ),
    LlmProvider(
        "openai", "OpenAI (GPT-4.1 mini)", "OpenAI", "gpt-4.1-mini", "openai", False,
        "https://api.openai.com/v1",
    ),
    LlmProvider(
        "mistral", "Mistral Large", "Mistral", "mistral-large-latest", "mistral", False,
        "https://api.mistral.ai/v1",
    ),
)

#: Not in `PROVIDERS` — a port of src/server/extraction/sample.ts, the counterpart to
#: transcription's sample provider. Exists so a fresh instance with no API key at all
#: still produces a *complete* result — transcript, action items, decisions, guardrail
#: violations, the board — rather than ending at a transcript with zero actions, which
#: makes the product look broken instead of unconfigured.
_SAMPLE_PROVIDER = LlmProvider(
    "sample", "Bundled sample (no API key)", "the bundled sample", "fixture", "local_whisper", True,
)

#: Sentences unique to the bundled transcription fixture (transcription.py's
#: `_SAMPLE_SCRIPT`), used to recognise it. Two independent matches are required so a
#: single coincidental phrase in a real transcript is not enough to trigger the fixture.
_SAMPLE_FINGERPRINTS = (
    "three things today",
    "revised numbers over to priya",
    "make that peter and jordan",
    "holding the vendor contract until legal signs off",
)


def _looks_like_sample(prompt: str) -> bool:
    haystack = prompt.lower()
    return sum(1 for f in _SAMPLE_FINGERPRINTS if f in haystack) >= 2


#: Hand-authored, deliberately including the awkward cases the extractor is judged on: a
#: superseded pair, a hedged LOW-confidence item, an owner nobody named, and an item with
#: nothing to execute. Byte-identical to the TS fixture.
_SAMPLE_FIXTURE: dict[str, Any] = {
    "summary": (
        "Reviewed the Q3 budget, agreed to hold the vendor renewal until legal signs off, and "
        "paused hiring for the analyst role until Q4. Follow-ups were assigned for the revised "
        "numbers and the renewal meeting."
    ),
    "actions": [
        {
            "description": "Send the revised Q3 budget numbers to Priya",
            "actionType": "TASK", "ownerName": "Speaker 2", "deadlineIso": None,
            "priority": "HIGH", "confidence": "HIGH", "sourceTimestampMs": 12_500,
            "sourceQuote": "On the budget — I'll get the revised numbers over to Priya by Friday.",
            "reasoning": "Speaker 2 committed to it directly and named a deadline.",
            "supersededByIndex": None,
        },
        {
            "description": "Schedule the Q3 budget review with Priya and Jordan",
            "actionType": "CALENDAR", "ownerName": "Speaker 1", "deadlineIso": None,
            "priority": "HIGH", "confidence": "HIGH", "sourceTimestampMs": 21_000,
            "sourceQuote": "Good. Let's lock a review for Friday morning, say ten, with Priya and Jordan.",
            "reasoning": "A specific time and attendee list were proposed and agreed in the next turn.",
            "supersededByIndex": None,
        },
        {
            # Superseded by index 3 — the pair the dashboard renders and POL_SUPERSEDED blocks.
            "description": "Set up the vendor renewal meeting with Peter",
            "actionType": "CALENDAR", "ownerName": "Speaker 2", "deadlineIso": None,
            "priority": "MEDIUM", "confidence": "MEDIUM", "sourceTimestampMs": 39_000,
            "sourceQuote": "Noted. Marcus, can you set up a meeting with Peter about the renewal?",
            "reasoning": "Revised eight seconds later when Jordan was added.",
            "supersededByIndex": 3,
        },
        {
            "description": "Set up the vendor renewal meeting with Peter and Jordan",
            "actionType": "CALENDAR", "ownerName": "Speaker 2", "deadlineIso": None,
            "priority": "MEDIUM", "confidence": "HIGH", "sourceTimestampMs": 47_000,
            "sourceQuote": "Sure. Actually, make that Peter and Jordan — Jordan owns the contract now.",
            "reasoning": "Corrects the earlier single-attendee version of the same meeting.",
            "supersededByIndex": None,
        },
        {
            # Hedged language => LOW, which keeps it out of the ready lane by construction.
            "description": "Email the vendor about the renewal delay",
            "actionType": "EMAIL", "ownerName": None, "deadlineIso": None,
            "priority": "MEDIUM", "confidence": "LOW", "sourceTimestampMs": 73_000,
            "sourceQuote": "Someone should probably email the vendor about the delay, I think.",
            "reasoning": "Hedged, unassigned, and explicitly deferred in the following turn.",
            "supersededByIndex": None,
        },
        {
            "description": "Write up what was agreed and circulate it",
            "actionType": "EMAIL", "ownerName": "Speaker 1", "deadlineIso": None,
            "priority": "MEDIUM", "confidence": "HIGH", "sourceTimestampMs": 82_000,
            "sourceQuote": (
                "Let's hold that until we know the new date. I'll write up what we agreed and "
                "send it round."
            ),
            "reasoning": "Speaker 1 undertook it in the same sentence.",
            "supersededByIndex": None,
        },
        {
            # Context worth recording with nothing to execute — the INFORMATIONAL lane.
            "description": "Note: hiring for the analyst role is paused until Q4 at the earliest",
            "actionType": "NONE", "ownerName": "Speaker 3", "deadlineIso": None,
            "priority": "LOW", "confidence": "HIGH", "sourceTimestampMs": 92_500,
            "sourceQuote": "One more — we're not opening the analyst role until Q4 at the earliest.",
            "reasoning": "A stated constraint. Nothing to do.",
            "supersededByIndex": None,
        },
    ],
    "decisions": [
        {
            "statement": "The vendor contract is on hold until legal signs off.",
            "decidedBy": "Speaker 3", "sourceTimestampMs": 56_000,
            "sourceQuote": "We are holding the vendor contract until legal signs off. That's decided.",
        },
        {
            "statement": "Hiring for the analyst role is deferred to Q4 at the earliest.",
            "decidedBy": "Speaker 3", "sourceTimestampMs": 92_500,
            "sourceQuote": "One more — we're not opening the analyst role until Q4 at the earliest.",
        },
    ],
}


async def sample_extract(user_prompt: str) -> dict[str, Any]:
    """A port of sample.ts's `extract()`. A fixture, not a model, and it refuses to
    pretend otherwise: it only answers for the bundled sample transcript, matched on
    sentences it knows. Given a real recording it fails with the same actionable message
    the registry would give for no provider at all — inventing plausible action items
    for audio it cannot read would be worse than any error message."""
    if not _looks_like_sample(user_prompt):
        raise ExtractionError(
            "sample_only",
            "No extraction provider is configured, and the bundled sample only understands "
            "the sample recording. Add a Groq or Gemini key in Settings → API keys — both are "
            "free — and this transcript will be read properly.",
        )
    await asyncio.sleep(0.4)
    # Deep-copied so a caller mutating the result (validate_extraction does not, but a
    # future one might) can never corrupt the shared fixture for the next request.
    return json.loads(json.dumps(_SAMPLE_FIXTURE))


_MODEL_UNAVAILABLE = re.compile(
    r"model_not_found|model_decommissioned|model[_ ]not[_ ]available|"
    r"does not exist or you do not have access",
    re.IGNORECASE,
)


def is_model_unavailable(status: int, body: str) -> bool:
    """Whether the provider is saying "no such model", in any of the shapes it says it in.

    Groq answers a bad model id with 404 + ``model_not_found``, and a *retired* one with 400
    + ``model_decommissioned``. Same problem from the user's side and the same fix, so both
    classify here.
    """
    if status == 404:
        return True
    return bool(_MODEL_UNAVAILABLE.search(body))


def model_unavailable_message(brand: str) -> str:
    return (
        f"The extraction model is unavailable. Please check the configured {brand} model and "
        "try again."
    )


def _http_error(provider: LlmProvider, status: int, body: str) -> ExtractionError:
    shared = {"status": status, "model": provider.model, "detail": body[:2000]}
    if is_model_unavailable(status, body):
        return ExtractionError(
            "model_unavailable", model_unavailable_message(provider.brand),
            # Repeating the identical request gets the identical 404. The user retries after
            # changing the model, which is a different request.
            retryable=False, **shared,
        )
    if status in (401, 403):
        return ExtractionError(
            f"{provider.id}_http_{status}",
            f"{provider.brand} rejected the API key. Check it in Settings → API keys.",
            retryable=False, **shared,
        )
    if status == 429:
        return ExtractionError(
            f"{provider.id}_http_{status}",
            f"{provider.brand} rate limit reached. Its free tier resets — try again shortly.",
            retryable=True, **shared,
        )
    return ExtractionError(
        f"{provider.id}_http_{status}",
        # Deliberately not the response body: see the module docstring.
        f"{provider.display_name} could not complete the request (HTTP {status}). Try again, or "
        "switch provider in Settings → API keys.",
        retryable=status in (408, 500, 502, 503, 504), **shared,
    )


class LlmService:
    def __init__(self, credentials: CredentialService, settings: Settings) -> None:
        self.credentials = credentials
        self.settings = settings

    async def resolve(
        self, user_id: str, *, allow_sample: bool = False
    ) -> tuple[LlmProvider, str, str]:
        """The first configured provider and its key, or the bundled sample as a last
        resort. Returns ``(provider, api_key, source)`` where source is
        ``"USER" | "ENV" | "SAMPLE"`` — a port of
        src/server/extraction/providers.ts::resolveExtractor.

        ``allow_sample`` defaults to False and stays that way for every caller except the
        ingest pipeline (`extraction.py::extract_into`): BRD generation has no bundled
        fixture to fall back to, and silently handing it one that only understands the
        sample meeting transcript would fail confusingly on real content instead of
        reporting "no provider configured" plainly.
        """
        for provider in PROVIDERS:
            resolved = await self.credentials.resolve(user_id, provider.credential_service)
            if resolved.api_key:
                return provider, resolved.api_key, resolved.source

        if allow_sample:
            return _SAMPLE_PROVIDER, "", "SAMPLE"

        from app.core.exceptions import service_unavailable

        raise service_unavailable(
            "extraction_unavailable",
            "No extraction provider is configured. Add a Groq or Gemini key in "
            "Settings → API keys — both are free and either covers this.",
            {
                "needs": [
                    {"service": p.credential_service, "displayName": p.display_name}
                    for p in PROVIDERS
                    if p.free_tier
                ]
            },
        )

    async def status(self, user_id: str) -> dict[str, Any]:
        candidates = []
        chosen = None
        for provider in PROVIDERS:
            resolved = await self.credentials.resolve(user_id, provider.credential_service)
            configured = bool(resolved.api_key)
            candidates.append(
                {
                    "service": provider.credential_service,
                    "displayName": provider.display_name,
                    "freeTier": provider.free_tier,
                    "configured": configured,
                }
            )
            if configured and chosen is None:
                chosen = (provider, resolved.source)
        return {
            "available": chosen is not None,
            "provider": chosen[0].display_name if chosen else None,
            "model": chosen[0].model if chosen else None,
            "source": chosen[1] if chosen else None,
            "candidates": candidates,
        }

    async def call_tool(
        self,
        user_id: str,
        *,
        system: str,
        user_prompt: str,
        tool: dict[str, Any],
        log: Optional[Logger] = None,
    ) -> tuple[Any, LlmProvider]:
        """One forced tool call, returning the raw arguments and the provider that served it."""
        provider, api_key, _source = await self.resolve(user_id)
        log = (log or logger).child(provider=provider.id, model=provider.model)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                if provider.kind == "gemini":
                    raw = await self._gemini(client, provider, api_key, system, user_prompt, tool)
                else:
                    raw = await self._openai(client, provider, api_key, system, user_prompt, tool)
        except httpx.TimeoutException as exc:
            raise ExtractionError(
                "timeout", f"{provider.display_name} did not respond in time.",
                retryable=True, model=provider.model,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExtractionError(
                "network_error", f"{provider.display_name} was unreachable.",
                retryable=True, model=provider.model,
            ) from exc

        return raw, provider

    async def _openai(self, client, provider, api_key, system, user_prompt, tool):
        response = await client.post(
            f"{provider.base_url}/chat/completions",
            headers={"authorization": f"Bearer {api_key}"},
            json={
                "model": provider.model,
                # Deterministic: two runs over the same transcript should not disagree about
                # what a meeting committed to.
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": tool["input_schema"],
                        },
                    }
                ],
                # Forced, so the model cannot answer in prose instead.
                "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
            },
        )
        if response.status_code >= 400:
            raise _http_error(provider, response.status_code, response.text)

        body = response.json()
        try:
            args = body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        except (KeyError, IndexError, TypeError):
            raise ExtractionError(
                "no_tool_call",
                f"{provider.display_name} did not return a structured result. Its model may not "
                "support tool use.",
                retryable=True, model=provider.model,
            ) from None
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            raise ExtractionError(
                "malformed_tool_arguments",
                f"{provider.display_name} returned unparseable arguments.",
                retryable=True, model=provider.model,
            ) from None

    async def _gemini(self, client, provider, api_key, system, user_prompt, tool):
        # Gemini's function declarations reject JSON Schema's `type: ["string","null"]` union
        # form, so nullable fields are declared plain and nullability is enforced on the way
        # back instead. Same contract, different dialect.
        declaration = json.loads(
            re.sub(r'\["(\w+)","null"\]', r'"\1"', json.dumps(tool["input_schema"]))
        )
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{provider.model}"
            f":generateContent",
            params={"key": api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": tool["name"],
                                "description": tool["description"],
                                "parameters": declaration,
                            }
                        ]
                    }
                ],
                "toolConfig": {
                    "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool["name"]]}
                },
                "generationConfig": {"temperature": 0},
            },
        )
        if response.status_code >= 400:
            raise _http_error(provider, response.status_code, response.text)

        body = response.json()
        for part in (body.get("candidates") or [{}])[0].get("content", {}).get("parts", []):
            if "functionCall" in part:
                return part["functionCall"].get("args")
        raise ExtractionError(
            "no_tool_call", f"{provider.display_name} did not return a structured result.",
            retryable=True, model=provider.model,
        )
