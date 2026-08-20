"""Preferences — SPEC-005 §4. These feed the rule engine, so a change here changes guardrails."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep
from app.services.settings_profile import SettingsService

router = APIRouter()

ActionType = Literal["CALENDAR", "TASK", "EMAIL", "REMINDER"]
ApprovalGate = Literal["AUTO", "EXPLICIT_APPROVAL", "EXPLICIT_APPROVAL_WITH_CONFIRMATION"]


class SettingsPatch(BaseModel):
    """A port of ``settingsSchema`` in ``src/server/settings/service.ts``.

    Only the constraints that are genuinely schema-level in the original — a bare type, a
    length, or (for `approvalThresholds`/`providerRouting`'s keys) an enum — are declared
    here. The clock format, the workday-ordering check, time zone existence, org-domain
    format, and provider-routing existence are all *explicit* checks in the original
    service function, not part of its zod schema, because they need a specific `422` code
    (`invalid_clock`, `invalid_workday`, ...) rather than a generic "the shape is wrong".
    A Pydantic ``Field(pattern=...)`` or raising ``ValueError`` from a validator both land
    in FastAPI's `RequestValidationError` handler, which always answers `400
    invalid_request` — the right shape for a genuine type mismatch (matching zod's own
    `ZodError` handling, see `src/lib/http.ts`), but the wrong one for these business
    rules. They are validated explicitly in `SettingsService.update` instead, each raising
    its own `unprocessable(code, ...)` — see the note there.
    """

    timeZone: Optional[str] = Field(default=None, max_length=64)
    workdayStart: Optional[str] = Field(default=None, max_length=8)
    workdayEnd: Optional[str] = Field(default=None, max_length=8)
    allowWeekends: Optional[bool] = None
    maxMeetingMinutes: Optional[int] = Field(default=None, ge=5, le=1440)
    minBufferMinutes: Optional[int] = Field(default=None, ge=0, le=240)
    orgDomains: Optional[list[str]] = Field(default=None, max_length=50)
    autoExecuteLowRisk: Optional[bool] = None
    budgetApprovalLimit: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    orgCurrency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    approvalThresholds: Optional[dict[ActionType, ApprovalGate]] = None
    providerRouting: Optional[dict[ActionType, str]] = None

    @field_validator("orgCurrency")
    @classmethod
    def _upper_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


@router.get("")
async def get_settings(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await SettingsService(session).get(user.id)


@router.patch("")
async def patch_settings(
    body: SettingsPatch, user: CurrentUser, session: SessionDep, request_id: RequestIdDep
) -> dict[str, Any]:
    result = await SettingsService(session).update(
        user.id, body.model_dump(exclude_unset=True, exclude_none=True), request_id
    )
    await session.commit()
    return result
