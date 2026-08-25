"""The FastAPI application.

Deployed on Render; the frontend is a different origin on Vercel, which drives two things
that would otherwise look like boilerplate:

* **CORS is load-bearing.** Without the correct origin list and ``allow_credentials``, the
  browser refuses every authenticated request — and it fails at the browser, so the server
  logs show nothing. ``Settings.cors_origins`` refuses ``*`` outright for this reason.
* **``x-request-id`` is echoed and honoured**, so one identifier spans a frontend request
  and the backend work it caused.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.errors import register_error_handlers
from app.api.routes import (
    action_items,
    audit,
    auth,
    brd,
    credentials,
    decisions,
    health,
    ingest,
    profile,
    search,
    settings as settings_routes,
    speech,
    team,
    transcripts,
)
from app.core.config import get_settings
from app.core.crypto import encryption_available
from app.core.logging import configure_logging, logger
from app.db.session import dispose_engine, init_engine, session_factory


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    init_engine(settings)

    # Fail visibly at boot rather than on the first request that needs the database.
    async with session_factory()() as session:
        await session.execute(text("SELECT 1"))

    logger.info(
        "api.started",
        env=settings.APP_ENV,
        corsOrigins=settings.cors_origins,
        encryptionConfigured=encryption_available(settings.APP_ENCRYPTION_KEY),
        # Absence here means every token request fails, so it is worth one line at boot.
        authConfigured=bool(settings.JWT_SECRET),
    )
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("api.stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Vowcraft API",
        version="1.0.0",
        description="Voice to business-requirements, action extraction, and guarded execution.",
        lifespan=lifespan,
        # Docs are useful in development and an information leak in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-request-id"],
        # So the frontend can read the id back and log the same trace identifier.
        expose_headers=["x-request-id"],
        max_age=600,
    )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        rid = incoming if incoming and len(incoming) <= 200 else f"req_{int(time.time()*1000):x}"
        request.state.request_id = rid
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["x-request-id"] = rid
        # One structured line per request, replacing uvicorn's unstructured access log.
        logger.info(
            "request.completed",
            requestId=rid,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            durationMs=duration_ms,
        )
        return response

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(speech.router, prefix="/api/speech", tags=["speech"])
    app.include_router(brd.router, prefix="/api/brd", tags=["brd"])
    # `ingest` before `transcripts`, deliberately: both mount at /api/transcripts, and
    # `ingest` declares the static path `/pipeline-status` while `transcripts` declares
    # the dynamic `/{transcript_id}`. Starlette matches routes in registration order, so
    # the dynamic one — mounted second — would otherwise shadow the static one, treating
    # "pipeline-status" as a transcript id and 404ing on a route that does exist.
    app.include_router(ingest.router, prefix="/api/transcripts", tags=["ingest"])
    app.include_router(transcripts.router, prefix="/api/transcripts", tags=["transcripts"])
    app.include_router(action_items.router, prefix="/api/action-items", tags=["action-items"])
    app.include_router(credentials.router, prefix="/api/credentials", tags=["credentials"])
    app.include_router(audit.router, prefix="/api/audit-log", tags=["audit"])
    app.include_router(decisions.router, prefix="/api/decisions", tags=["decisions"])
    app.include_router(search.router, prefix="/api/search", tags=["search"])
    app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
    app.include_router(settings_routes.router, prefix="/api/settings", tags=["settings"])
    app.include_router(team.router, prefix="/api/team", tags=["team"])
    return app


app = create_app()
