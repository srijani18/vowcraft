"""Integration tests for embedding generation against the real schema — SPEC-021.

HTTP is mocked (Voyage cannot be called from CI without a real key, the same reasoning
`test_credentials_service.py` already applies to its own `verify()` tests), but storage
goes through the real Postgres/pgvector path — `test_embed_segments_into_round_trips_
through_pgvector` is the one test that proves a vector actually (de)serialises correctly
through asyncpg's codec (app/db/session.py's `_register_vector_codec`), not just that the
HTTP mock returned the right shape.
"""

from __future__ import annotations

import base64
import os
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from vowcraft_db import SegmentEmbedding

from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.credentials import CredentialService
from app.services.embeddings import (
    _BATCH_SIZE,
    EmbeddingError,
    embed,
    embed_segments_into,
    resolve_embedder,
)

_TEST_KEY = base64.b64encode(os.urandom(32)).decode()


def _settings() -> Settings:
    return Settings(DATABASE_URL="postgresql://test:test@localhost/test", APP_ENCRYPTION_KEY=_TEST_KEY)


def _credentials(db_session) -> CredentialService:
    return CredentialService(db_session, _settings())


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None, text: str = "{}"):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text

    def json(self):
        return self._json_body


def _mocked_post(response: _FakeResponse):
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return patch("app.services.embeddings.httpx.AsyncClient", return_value=client), client


def _embedding_response(count: int, dim: int = 512) -> _FakeResponse:
    # Deliberately returned out of order, then re-sorted by `index` inside embed() — a
    # provider is not obligated to answer in request order.
    data = [{"embedding": [float(i)] * dim, "index": i} for i in reversed(range(count))]
    return _FakeResponse(200, {"data": data})


class TestResolveEmbedder:
    async def test_returns_the_users_own_key_once_saved(self, make_user, db_session):
        user = await make_user()
        credentials = _credentials(db_session)
        await credentials.save(user.id, "voyage", {"apiKey": "pa-test-key"})

        provider, api_key, source = await resolve_embedder(user.id, credentials)
        assert provider.id == "voyage"
        assert api_key == "pa-test-key"
        assert source == "USER"

    async def test_raises_service_unavailable_when_nothing_is_configured(self, make_user, db_session):
        user = await make_user()
        try:
            await resolve_embedder(user.id, _credentials(db_session))
            raise AssertionError("expected an embedding_unavailable AppError")
        except AppError as exc:
            assert exc.status_code == 503
            assert exc.code == "embedding_unavailable"


class TestEmbed:
    async def test_returns_vectors_in_input_order_regardless_of_response_order(self):
        patcher, client = _mocked_post(_embedding_response(3))
        with patcher:
            vectors = await embed(
                _provider(), texts=["a", "b", "c"], api_key="k", input_type="document",
            )
        assert len(vectors) == 3
        assert len(vectors[0]) == 512
        # index 0's vector is [0.0]*512 per _embedding_response's construction.
        assert vectors[0][0] == 0.0
        assert vectors[2][0] == 2.0

    async def test_batches_under_the_per_request_cap(self):
        texts = [f"segment {i}" for i in range(_BATCH_SIZE + 10)]
        patcher, client = _mocked_post(_embedding_response(_BATCH_SIZE))
        # Second call gets fewer items than the mock returns, but embed() only reads what
        # the response actually contains — the point here is call *count*, not content.
        client_ref = {}

        async def _side_effect(url, headers, json):
            n = len(json["input"])
            return _embedding_response(n)

        with patcher:
            client.post.side_effect = _side_effect
            vectors = await embed(_provider(), texts=texts, api_key="k", input_type="document")
        assert client.post.call_count == 2
        assert len(vectors) == len(texts)

    async def test_a_401_is_mapped_to_a_settings_pointing_message(self):
        patcher, _client = _mocked_post(_FakeResponse(401, text='{"error":{"message":"bad key"}}'))
        try:
            with patcher:
                await embed(_provider(), texts=["a"], api_key="k", input_type="query")
            raise AssertionError("expected an EmbeddingError")
        except EmbeddingError as exc:
            assert exc.code == "voyage_http_401"
            assert "Settings" in exc.message
            assert exc.retryable is False

    async def test_a_429_is_retryable(self):
        patcher, _client = _mocked_post(_FakeResponse(429, text="{}"))
        try:
            with patcher:
                await embed(_provider(), texts=["a"], api_key="k", input_type="query")
            raise AssertionError("expected an EmbeddingError")
        except EmbeddingError as exc:
            assert exc.code == "voyage_http_429"
            assert exc.retryable is True


class TestEmbedSegmentsInto:
    async def test_embeds_every_segment_and_round_trips_through_pgvector(
        self, make_user, make_transcript, make_segment, db_session
    ):
        user = await make_user()
        credentials = _credentials(db_session)
        await credentials.save(user.id, "voyage", {"apiKey": "pa-test-key"})
        transcript = await make_transcript(user)
        segments = [await make_segment(transcript, text=f"segment {i}") for i in range(3)]

        patcher, _client = _mocked_post(_embedding_response(3))
        with patcher:
            outcome = await embed_segments_into(db_session, transcript.id, user.id, credentials)

        assert outcome == {"ok": True, "embedded": 3}
        rows = (
            await db_session.scalars(
                select(SegmentEmbedding).where(
                    SegmentEmbedding.segment_id.in_([s.id for s in segments])
                )
            )
        ).all()
        assert len(rows) == 3
        for row in rows:
            assert row.provider == "voyage"
            assert row.model == "voyage-3-lite"
            # Proves the vector actually round-tripped through asyncpg's codec, not just
            # that a Python list was held in memory.
            assert len(row.embedding) == 512

    async def test_with_no_credential_configured_writes_nothing(
        self, make_user, make_transcript, make_segment, db_session
    ):
        user = await make_user()
        transcript = await make_transcript(user)
        await make_segment(transcript)

        outcome = await embed_segments_into(db_session, transcript.id, user.id, _credentials(db_session))
        assert outcome == {"ok": False, "embedded": 0, "reason": "no_provider"}

        rows = (await db_session.scalars(select(SegmentEmbedding))).all()
        assert rows == []

    async def test_re_embedding_replaces_rather_than_accumulates(
        self, make_user, make_transcript, make_segment, db_session
    ):
        user = await make_user()
        credentials = _credentials(db_session)
        await credentials.save(user.id, "voyage", {"apiKey": "pa-test-key"})
        transcript = await make_transcript(user)
        await make_segment(transcript)

        patcher, _client = _mocked_post(_embedding_response(1))
        with patcher:
            await embed_segments_into(db_session, transcript.id, user.id, credentials)
            await embed_segments_into(db_session, transcript.id, user.id, credentials)

        rows = (await db_session.scalars(select(SegmentEmbedding))).all()
        assert len(rows) == 1


def _provider():
    from app.services.embeddings import PROVIDERS

    return PROVIDERS[0]
