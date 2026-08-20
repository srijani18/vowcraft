"""BYOK credentials — SPEC-004.

The one rule that shapes every response here: **a stored secret is never returned**. The
list endpoint reports whether a key exists, which tier supplied it, and a masked preview —
enough to recognise which key is stored, never enough to use it.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep, SettingsDep
from app.core.crypto import encryption_available
from app.services.credentials import CredentialService

router = APIRouter()


class SaveBody(BaseModel):
    # A dict rather than a fixed `apiKey`, because some services need more than one field
    # and the catalogue is the authority on which.
    secrets: dict[str, str] = Field(min_length=1)
    label: Optional[str] = Field(default=None, max_length=120)
    enabled: Optional[bool] = None


@router.get("")
async def list_credentials(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    service = CredentialService(session, settings)
    credentials, modules = [
        await service.list_views(user.id),
        await service.module_availability(user.id),
    ]
    return {
        "credentials": credentials,
        "modules": modules,
        "encryptionConfigured": encryption_available(settings.APP_ENCRYPTION_KEY),
    }


@router.get("/status")
async def credentials_status(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Drives the availability banner (SPEC-004 §7)."""
    service = CredentialService(session, settings)
    return {
        "modules": await service.module_availability(user.id),
        "integrationsMode": settings.INTEGRATIONS_MODE,
    }


@router.put("/{service}")
async def save_credential(
    service: str, body: SaveBody, user: CurrentUser, session: SessionDep, settings: SettingsDep,
    request_id: RequestIdDep,
) -> dict[str, Any]:
    """Write-only; there is no plaintext read path."""
    result = await CredentialService(session, settings).save(
        user.id, service, body.secrets, label=body.label, enabled=body.enabled, request_id=request_id
    )
    await session.commit()
    return result


@router.delete("/{service}")
async def delete_credential(
    service: str, user: CurrentUser, session: SessionDep, settings: SettingsDep, request_id: RequestIdDep
) -> dict[str, Any]:
    result = await CredentialService(session, settings).delete(user.id, service, request_id)
    await session.commit()
    return result


@router.post("/{service}/verify")
async def verify_credential(
    service: str, user: CurrentUser, session: SessionDep, settings: SettingsDep, request_id: RequestIdDep
) -> dict[str, Any]:
    """SPEC-004 §8."""
    result = await CredentialService(session, settings).verify(user.id, service, request_id)
    await session.commit()
    return result
