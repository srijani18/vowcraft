"""Auth routes — bind, authenticate, delegate, translate. No business logic here.

Ports ``src/app/api/auth/*``. Every handler is a few lines because the rules live in
``services/auth.py`` and the policy in ``domain/password_rules.py``; a route that grew to
inspect a password would be a layering mistake, not a shortcut.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, RequestIdDep, SessionDep, SettingsDep
from app.core.exceptions import unauthorized
from app.core.logging import logger
from app.core.security import TokenError, claims_match_user, decode_token
from app.db.repositories.users import AuditRepository, UserRepository
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenPair,
    UserView,
)
from app.services.auth import AuthService, to_view

router = APIRouter()


def _service(session: AsyncSession, settings, request_id: str) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        audit=AuditRepository(session),
        settings=settings,
        log=logger.child(requestId=request_id),
        request_id=request_id,
    )


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest, session: SessionDep, settings: SettingsDep, request_id: RequestIdDep
) -> AuthResponse:
    result = await _service(session, settings, request_id).signup(
        email=body.email, password=body.password, name=body.name
    )
    # One commit per request, at the boundary: the account row, its settings and the audit
    # entry land together or not at all. A user without settings would break the rule
    # engine on their first action.
    await session.commit()
    return result


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest, session: SessionDep, settings: SettingsDep, request_id: RequestIdDep
) -> AuthResponse:
    result = await _service(session, settings, request_id).login(
        email=body.email, password=body.password
    )
    # Committed even on the failure path inside the service — the failed-attempt audit row
    # is the point. A rejected login that leaves no trace is not auditable.
    await session.commit()
    return result


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest, session: SessionDep, settings: SettingsDep, request_id: RequestIdDep
) -> TokenPair:
    """Exchange a refresh token for a new pair.

    The refresh token is re-checked against the user's current ``passwordUpdatedAt``, so a
    password change invalidates it too. Otherwise a stolen refresh token would outlive the
    password rotation meant to stop it — which is the whole point of the claim.
    """
    try:
        claims = decode_token(
            body.refreshToken,
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            expected_type="refresh",
        )
    except TokenError:
        raise unauthorized("That session has expired. Sign in again.")

    users = UserRepository(session)
    user = await users.by_id(claims.subject)
    if user is None or not claims_match_user(claims, user.password_updated_at):
        raise unauthorized("That session has expired. Sign in again.")

    return _service(session, settings, request_id).issue_pair(user)


@router.get("/me", response_model=UserView)
async def me(user: CurrentUser) -> UserView:
    return to_view(user)


@router.post("/logout")
async def logout(user: CurrentUser, session: SessionDep, request_id: RequestIdDep) -> dict:
    """Records the intent; the client discards its tokens.

    Stated plainly because it matters: these are stateless bearer tokens, so this cannot
    invalidate an access token that has already been issued — it expires on its own
    schedule (30 minutes by default). What *does* revoke immediately is a password change,
    via the ``pwd`` claim. Per-device revocation needs a token denylist, which is on the
    roadmap and not pretended here.
    """
    await AuditRepository(session).record(
        event="auth.signed_out", actor_id=user.id, request_id=request_id
    )
    await session.commit()
    return {"ok": True}


@router.post("/change-password", response_model=TokenPair)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request_id: RequestIdDep,
) -> TokenPair:
    tokens = await _service(session, settings, request_id).change_password(
        user, current=body.currentPassword, new=body.newPassword
    )
    await session.commit()
    return tokens
