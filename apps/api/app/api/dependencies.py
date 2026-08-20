"""Request-scoped dependencies: settings, session, request id, current user.

Authentication is a dependency rather than middleware so that a route declares whether it
needs a user in its signature. Middleware that authenticates everything then exempts a
path list is the shape where a new public route accidentally becomes authenticated, or —
much worse — a new private route accidentally does not.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from voice2brd_db import User

from app.core.config import Settings, get_settings
from app.core.exceptions import unauthorized
from app.core.security import TokenError, claims_match_user, decode_token
from app.db.session import get_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def request_id(request: Request) -> str:
    """Honour an inbound ``x-request-id`` so a trace spans frontend and backend."""
    incoming = request.headers.get("x-request-id")
    if incoming and len(incoming) <= 200:
        return incoming
    return f"req_{uuid.uuid4().hex[:16]}"


RequestIdDep = Annotated[str, Depends(request_id)]


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def optional_user(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[User]:
    """The signed-in user, or None.

    A *present but invalid* token returns None rather than falling back to anything. The
    Next.js implementation had a development identity fallback here, and it caused real
    confusion: a dropped cookie silently substituted the seeded demo account, so the
    profile page showed someone else's name and the user reported it as a login bug. There
    is no fallback in this service.
    """
    token = _bearer(authorization)
    if not token:
        return None
    try:
        claims = decode_token(
            token,
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            expected_type="access",
        )
    except TokenError:
        return None

    user = await session.scalar(select(User).where(User.id == claims.subject))
    if user is None:
        return None
    # A validly-signed token is not enough: it may predate a password change.
    if not claims_match_user(claims, user.password_updated_at):
        return None
    # A pending deletion deliberately does NOT block authentication. The grace period
    # exists so the user can sign in and cancel; refusing them here would make the window
    # decorative and the deletion irreversible.
    return user


async def current_user(user: Annotated[Optional[User], Depends(optional_user)]) -> User:
    if user is None:
        raise unauthorized("Sign in to continue.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[Optional[User], Depends(optional_user)]
