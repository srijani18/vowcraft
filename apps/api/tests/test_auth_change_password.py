"""Integration tests for ``AuthService.change_password`` — SPEC-005 §3.

Before this fix, an account with no password yet (a Google-only sign-in setting one for
the first time) could not use this endpoint at all: it unconditionally required
``currentPassword`` and then refused outright with "This account signs in with Google. Set
a password in Settings → Profile" — the exact action the caller was trying to perform,
with no way out. A wrong current password also answered `401 unauthenticated` rather than
`403 current_password_incorrect`, which would have made the browser's client silently
attempt a token refresh and retry the same failing request. Every test below exists because
the comparison against ``src/server/profile/service.ts``'s ``changePassword`` found a real
divergence at that exact spot.
"""

from __future__ import annotations

import pytest
from voice2brd_db import AuditLog
from sqlalchemy import select

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.logging import Logger
from app.core.passwords import hash_password, verify_password
from app.db.repositories.users import AuditRepository, UserRepository
from app.services.auth import AuthService


def _service(db_session, request_id: str = "req_1") -> AuthService:
    return AuthService(
        users=UserRepository(db_session),
        audit=AuditRepository(db_session),
        settings=Settings(),
        log=Logger({"service": "test"}),
        request_id=request_id,
    )


class TestFirstTimePasswordSet:
    async def test_an_account_with_no_password_can_set_one_without_a_current_password(
        self, db_session, make_user
    ):
        user = await make_user(password_hash=None)
        tokens = await _service(db_session).change_password(user, current=None, new="a genuinely long passphrase")
        assert tokens.accessToken
        assert verify_password("a genuinely long passphrase", user.password_hash)

    async def test_the_audit_row_records_it_as_a_first_time_set(self, db_session, make_user):
        user = await make_user(password_hash=None)
        await _service(db_session).change_password(user, current=None, new="a genuinely long passphrase")
        row = (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.actor_id == user.id, AuditLog.event == "profile.password_changed")
            )
        ).one()
        assert row.metadata_ == {"firstTime": True}


class TestChangingAnExistingPassword:
    async def test_the_current_password_is_required(self, db_session, make_user):
        user = await make_user(password_hash=hash_password("the current passphrase"))
        with pytest.raises(AppError) as exc:
            await _service(db_session).change_password(user, current=None, new="a new long passphrase")
        assert exc.value.status_code == 403
        assert exc.value.code == "current_password_required"

    async def test_a_wrong_current_password_is_refused_as_403_not_401(self, db_session, make_user):
        # 401 would make the browser's `apiFetch` treat this as an expired session and
        # silently attempt a token refresh + retry, masking the real error.
        user = await make_user(password_hash=hash_password("the current passphrase"))
        with pytest.raises(AppError) as exc:
            await _service(db_session).change_password(user, current="wrong", new="a new long passphrase")
        assert exc.value.status_code == 403
        assert exc.value.code == "current_password_incorrect"

    async def test_a_correct_change_succeeds_and_the_old_password_no_longer_verifies(
        self, db_session, make_user
    ):
        user = await make_user(password_hash=hash_password("the current passphrase"))
        await _service(db_session).change_password(
            user, current="the current passphrase", new="a brand new passphrase"
        )
        assert verify_password("a brand new passphrase", user.password_hash)
        assert not verify_password("the current passphrase", user.password_hash)

    async def test_reusing_the_same_password_is_refused(self, db_session, make_user):
        user = await make_user(password_hash=hash_password("the current passphrase"))
        with pytest.raises(AppError) as exc:
            await _service(db_session).change_password(
                user, current="the current passphrase", new="the current passphrase"
            )
        assert exc.value.status_code == 422
        assert exc.value.code == "password_unchanged"

    async def test_a_new_password_still_has_to_meet_the_strength_policy(self, db_session, make_user):
        user = await make_user(password_hash=hash_password("the current passphrase"))
        with pytest.raises(AppError) as exc:
            await _service(db_session).change_password(user, current="the current passphrase", new="short")
        assert exc.value.status_code == 422

    async def test_rotating_the_password_issues_a_fresh_token_pair(self, db_session, make_user):
        user = await make_user(password_hash=hash_password("the current passphrase"))
        tokens = await _service(db_session).change_password(
            user, current="the current passphrase", new="a brand new passphrase"
        )
        assert tokens.accessToken and tokens.refreshToken
