"""Liveness and readiness.

Two endpoints, because they answer different questions and Render uses them differently:

* ``/health`` — is the process up? No database call, so a database blip does not cause
  the platform to restart a perfectly healthy container.
* ``/health/ready`` — can it actually serve? Touches the database, and is what a
  deployment gate should watch.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.dependencies import SessionDep, SettingsDep
from app.core.crypto import encryption_available

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: SessionDep, settings: SettingsDep) -> dict:
    import time

    started = time.perf_counter()
    await session.execute(text("SELECT 1"))
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "status": "ok",
        "database": "up",
        "latencyMs": latency_ms,
        # Reported so a misconfigured deployment is visible before a user hits it, not
        # secret in any sense — it says whether a key is present, never what it is.
        "encryptionConfigured": encryption_available(settings.APP_ENCRYPTION_KEY),
        "authConfigured": bool(settings.JWT_SECRET),
    }
