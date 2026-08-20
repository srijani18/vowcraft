"""Profile — SPEC-005 §2, §3, §6, §8."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep
from app.services.settings_profile import ProfileService

router = APIRouter()


class RenameBody(BaseModel):
    name: str = Field(max_length=200)


class OnboardingBody(BaseModel):
    action: Literal["complete", "skip", "restart"]
    #: Which step they reached, for tuning the tour.
    reachedStep: Optional[int] = Field(default=None, ge=0, le=50)


class DeleteAccountBody(BaseModel):
    #: The account email, typed out. A checkbox is too easy to click through.
    confirm: str = Field(min_length=1)


@router.get("")
async def get_profile(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await ProfileService(session).get(user)


@router.patch("")
async def rename(
    body: RenameBody, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    result = await ProfileService(session).rename(user, body.name, request_id)
    await session.commit()
    return result


@router.post("/onboarding")
async def onboarding(
    body: OnboardingBody, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    result = await ProfileService(session).complete_onboarding(
        user, action=body.action, reached_step=body.reachedStep, request_id=request_id
    )
    await session.commit()
    return result


@router.delete("")
async def request_deletion(
    body: DeleteAccountBody, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    """Schedules deletion; does not perform it.

    DELETE rather than POST because the intent is deletion, even though the effect is
    deferred — and the grace period is what makes an accidental click recoverable.
    """
    result = await ProfileService(session).request_deletion(user, body.confirm, request_id)
    await session.commit()
    return result


@router.post("/restore")
async def cancel_deletion(
    user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    result = await ProfileService(session).cancel_deletion(user, request_id)
    await session.commit()
    return result
