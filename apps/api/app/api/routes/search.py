"""Semantic search — SPEC-021."""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep, SettingsDep
from app.core.logging import logger
from app.db.session import session_factory
from app.services.credentials import CredentialService
from app.services.embeddings import embed, embed_segments_into, resolve_embedder
from app.services.search import SearchService

router = APIRouter()

#: Kept alive the same way ingest.pipeline's background tasks are — a bare
#: `asyncio.create_task` reference is eligible for garbage collection the moment this
#: module's function returns, which can cancel the task mid-flight.
_LIVE_REINDEX_TASKS: set[asyncio.Task] = set()


@router.get("")
async def search(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    transcriptId: Annotated[Optional[str], Query()] = None,
) -> dict:
    credentials = CredentialService(session, settings)
    provider, api_key, _source = await resolve_embedder(user.id, credentials)
    [query_vector] = await embed(provider, texts=[q], api_key=api_key, input_type="query")
    return await SearchService(session).search(
        user.id, query_vector, transcript_id=transcriptId, limit=limit,
    )


@router.post("/reindex")
async def reindex(user: CurrentUser, session: SessionDep, settings: SettingsDep, request_id: RequestIdDep) -> dict:
    """Embeds every transcript that has segments with no embedding yet.

    Fire-and-forget, same shape as `ingest.pipeline.start_processing`: embedding a user's
    full backlog can take longer than one request should block for, so this answers
    immediately and the work continues after the response goes out.
    """
    from vowcraft_db import Segment, SegmentEmbedding, Transcript

    transcript_ids = (
        await session.scalars(
            select(Transcript.id)
            .join(Segment, Segment.transcript_id == Transcript.id)
            .outerjoin(SegmentEmbedding, SegmentEmbedding.segment_id == Segment.id)
            .where(Transcript.user_id == user.id, SegmentEmbedding.id.is_(None))
            .distinct()
        )
    ).all()

    task = asyncio.create_task(_run_reindex(list(transcript_ids), user.id, request_id))
    _LIVE_REINDEX_TASKS.add(task)
    task.add_done_callback(_LIVE_REINDEX_TASKS.discard)

    return {"status": "started", "transcripts": len(transcript_ids)}


async def _run_reindex(transcript_ids: list[str], user_id: str, request_id: str) -> None:
    log = logger.child(requestId=request_id, userId=user_id)
    from app.core.config import get_settings

    settings = get_settings()
    embedded_total = 0
    for transcript_id in transcript_ids:
        try:
            async with session_factory()() as session:
                outcome = await embed_segments_into(
                    session, transcript_id, user_id, CredentialService(session, settings)
                )
                embedded_total += outcome.get("embedded", 0)
        except Exception as exc:  # noqa: BLE001 — one transcript's failure must not stop the rest
            log.error("search.reindex_failed", transcriptId=transcript_id, err=exc)
    log.info("search.reindexed", transcripts=len(transcript_ids), embedded=embedded_total)
