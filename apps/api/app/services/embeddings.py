"""Segment embeddings via Voyage AI — SPEC-021.

Mirrors ``transcription.py``'s shape deliberately: a small ``PROVIDERS`` tuple (today,
exactly one entry) resolved through the same BYOK vault, not
``CredentialService.resolve_module``, which would silently hand back the unimplemented
``local_bge`` catalog entry as "configured" — that entry is ``local_only`` and always
resolves, even though nothing actually generates an embedding through it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy import delete, select
from vowcraft_db import Segment, SegmentEmbedding

from app.core.exceptions import service_unavailable
from app.services.credentials import CredentialService

_TIMEOUT_SECONDS = 60
#: Headroom under Voyage's documented 128-texts-per-request cap.
_BATCH_SIZE = 96


class EmbeddingError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class EmbeddingProvider:
    id: str
    display_name: str
    base_url: str
    model: str
    dimensions: int
    credential_service: str


#: One entry today, deliberately — see the module docstring. Shaped as a tuple so adding a
#: second provider later (Jina, local BGE) is additive, matching transcription.py's own
#: multi-provider fallback shape, without generalising anything not yet needed.
PROVIDERS: tuple[EmbeddingProvider, ...] = (
    EmbeddingProvider(
        "voyage", "Voyage AI", "https://api.voyageai.com/v1", "voyage-3-lite", 512, "voyage",
    ),
)


async def resolve_embedder(
    user_id: str, credentials: CredentialService
) -> tuple[EmbeddingProvider, str, str]:
    """The first configured embedding provider and its key.

    Returns ``(provider, api_key, source)`` where source is ``"USER" | "ENV"``. Unlike
    ``resolve_transcriber``, there is no sample fallback: a fabricated embedding would rank
    as confidently as a real one, which is a worse failure mode than refusing outright.
    """
    for provider in PROVIDERS:
        resolved = await credentials.resolve(user_id, provider.credential_service)
        if resolved.api_key:
            return provider, resolved.api_key, resolved.source

    raise service_unavailable(
        "embedding_unavailable",
        "Semantic search needs an embedding provider. Add a Voyage AI key in "
        "Settings → API keys — its free tier needs no card.",
        {
            "needs": [
                {"service": p.credential_service, "displayName": p.display_name}
                for p in PROVIDERS
            ]
        },
    )


async def embed(
    provider: EmbeddingProvider,
    *,
    texts: list[str],
    api_key: str,
    input_type: Literal["document", "query"],
) -> list[list[float]]:
    """Embeds ``texts`` in Voyage's own input order, batched under its per-request cap.

    ``input_type`` matters beyond documentation: Voyage's asymmetric retrieval tuning
    expects the short query and the long indexed passages embedded with different
    ``input_type`` values — using the wrong one silently degrades ranking quality without
    ever raising an error, so every caller must say which one it means.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{provider.base_url}/embeddings",
                    headers={"authorization": f"Bearer {api_key}"},
                    json={"input": batch, "model": provider.model, "input_type": input_type},
                )
        except httpx.TimeoutException as exc:
            raise EmbeddingError(
                "timeout", f"{provider.display_name} did not respond in time.", True
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                "network_error", f"{provider.display_name} was unreachable.", True
            ) from exc

        if response.status_code >= 400:
            detail = response.text[:300]
            try:
                detail = json.loads(response.text).get("error", {}).get("message", detail)
            except Exception:  # noqa: BLE001 — best-effort detail extraction only
                pass
            if response.status_code == 401:
                message = f"{provider.display_name} rejected the API key. Check it in Settings → API keys."
            elif response.status_code == 429:
                message = f"{provider.display_name} rate limit reached — try again shortly."
            else:
                message = f"{provider.display_name} returned {response.status_code}: {detail}"
            raise EmbeddingError(
                f"{provider.id}_http_{response.status_code}", message,
                response.status_code in (408, 429, 500, 502, 503, 504),
            )

        body = response.json()
        ordered = sorted(body["data"], key=lambda d: d["index"])
        vectors.extend(item["embedding"] for item in ordered)
    return vectors


async def embed_segments_into(
    session, transcript_id: str, user_id: str, credentials: CredentialService
) -> dict:
    """Best-effort: mirrors ``extraction.py``'s outcome-dict-not-exception contract, so a
    caller (the ingest pipeline) never has to special-case this step to stay unblocked —
    a transcript with no embedding provider configured still finishes as ``READY``."""
    try:
        provider, api_key, _source = await resolve_embedder(user_id, credentials)
    except Exception:  # noqa: BLE001 — an AppError here means "skip", not "fail the pipeline"
        return {"ok": False, "embedded": 0, "reason": "no_provider"}

    segments = list(
        await session.scalars(select(Segment).where(Segment.transcript_id == transcript_id))
    )
    if not segments:
        return {"ok": True, "embedded": 0}

    try:
        vectors = await embed(
            provider, texts=[s.text for s in segments], api_key=api_key, input_type="document"
        )
    except EmbeddingError as exc:
        return {"ok": False, "embedded": 0, "reason": exc.code}

    # Delete-then-insert, not update-in-place — see SegmentEmbedding's docstring: there is
    # no partial-embedding state worth representing.
    await session.execute(
        delete(SegmentEmbedding).where(
            SegmentEmbedding.segment_id.in_([s.id for s in segments])
        )
    )
    for segment, vector in zip(segments, vectors):
        session.add(
            SegmentEmbedding(
                segment_id=segment.id, embedding=vector, provider=provider.id, model=provider.model,
            )
        )
    await session.commit()
    return {"ok": True, "embedded": len(segments)}
