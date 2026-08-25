"""``OAuthTokenService`` against the real schema — SPEC-002 §4.

Every test here corresponds to a bug that actually shipped and reached a user:

* **The AAD contract.** ``persistTokens`` in src/integrations/oauth.ts binds token
  ciphertext with ``f"{user_id}:{provider_id}"``. This service read it back with an
  ``oauth:`` prefix, so AES-GCM authentication failed on every live execution and the
  adapter reported a correctly-connected Google Calendar as "not connected". The
  ``test_decrypts_a_token_written_with_the_typescript_aad`` case fails the moment either
  side of that contract drifts again.

* **No refresh at all.** This service used to decrypt whatever was stored and hand it
  over. Google issues one-hour tokens, so live mode worked for an hour after connecting
  and then failed until the user reconnected by hand.

* **"Not connected" vs "reconnect me".** Returning ``None`` for a *connected* account
  whose token could not be made usable told the user the opposite of what was true.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
from vowcraft_db import IntegrationAccount

from app.core.config import Settings
from app.core.crypto import encrypt
from app.core.exceptions import AppError
from app.integrations.oauth import OAuthTokenService, _aad
from app.integrations.registry import REGISTRY

#: A real key, generated per run. `api_settings` reads the ambient environment, which has
#: no APP_ENCRYPTION_KEY in CI — and these tests are specifically about the sealed bytes.
_TEST_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def api_settings() -> Settings:
    """Shadows conftest's fixture so every service and every fixture below share one key."""
    return Settings(APP_ENCRYPTION_KEY=_TEST_KEY)


CALENDAR = REGISTRY["google_calendar"]
#: Slack: connected, non-expiring token, no refresh endpoint. The "never needs refreshing"
#: branch has to be exercised by a provider that really is in that position.
SLACK = REGISTRY["slack"]


def _naive(dt: datetime) -> datetime:
    """`expiresAt` is `timestamp without time zone`; asyncpg refuses an aware value."""
    return dt.replace(tzinfo=None)


@pytest.fixture
def make_account(db_session, api_settings):
    async def _make(
        user,
        provider: str = "google_calendar",
        *,
        access_token: str = "ya29.stored-access-token",
        refresh_token: str | None = "1//stored-refresh-token",
        expires_in_seconds: int | None = 3600,
        needs_reauth: bool = False,
        aad: str | None = None,
    ) -> IntegrationAccount:
        bind = aad if aad is not None else _aad(user.id, provider)
        key = api_settings.APP_ENCRYPTION_KEY
        row = IntegrationAccount(
            user_id=user.id,
            provider=provider,
            access_token_enc=encrypt(access_token, key, bind),
            refresh_token_enc=encrypt(refresh_token, key, bind) if refresh_token else None,
            expires_at=(
                _naive(datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds))
                if expires_in_seconds is not None
                else None
            ),
            needs_reauth=needs_reauth,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


class TestTheCrossServiceAadContract:
    async def test_decrypts_a_token_written_with_the_typescript_aad(
        self, make_user, make_account, db_session, api_settings
    ):
        """The whole point: a token sealed exactly as `persistTokens` seals it must read
        back here. This is the regression guard for the "not connected" bug."""
        user = await make_user()
        await make_account(user, access_token="ya29.written-by-nextjs")

        token = await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, CALENDAR
        )

        assert token == "ya29.written-by-nextjs"

    async def test_a_token_sealed_with_a_different_aad_asks_for_a_reconnect(
        self, make_user, make_account, db_session, api_settings
    ):
        """A mismatched AAD is indistinguishable from a rotated APP_ENCRYPTION_KEY, and
        both need the user back — not a silent None that reads as "never connected"."""
        user = await make_user()
        await make_account(user, aad=f"oauth:{'x' * 8}:google_calendar")

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "token_undecryptable"
        assert "reconnect" in excinfo.value.message.lower()


class TestWhenNoRefreshIsNeeded:
    async def test_a_token_comfortably_inside_its_lifetime_is_returned_as_is(
        self, make_user, make_account, db_session, api_settings
    ):
        user = await make_user()
        await make_account(user, access_token="ya29.still-good", expires_in_seconds=3600)

        token = await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, CALENDAR
        )

        assert token == "ya29.still-good"

    async def test_a_null_expiry_means_the_token_never_expires(
        self, make_user, make_account, db_session, api_settings
    ):
        """Slack and Notion issue non-expiring tokens. Treating a null expiry as "expired"
        would send every one of them down a refresh path they cannot support."""
        user = await make_user()
        await make_account(
            user, provider="slack", access_token="xoxb-forever", expires_in_seconds=None
        )

        token = await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, SLACK
        )

        assert token == "xoxb-forever"


class TestNothingConnected:
    async def test_no_account_is_none_rather_than_an_error(
        self, make_user, db_session, api_settings
    ):
        """`None` is reserved for genuinely-not-connected, which is what lets the adapter
        say "connect it in Settings" instead of "reconnect it"."""
        user = await make_user()

        token = await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, CALENDAR
        )

        assert token is None

    async def test_an_account_already_flagged_for_reauth_says_so(
        self, make_user, make_account, db_session, api_settings
    ):
        user = await make_user()
        await make_account(user, needs_reauth=True)

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "reauth_required"


class TestExpiryWithoutARefreshPath:
    async def test_an_expired_token_with_no_refresh_token_flags_reauth(
        self, make_user, make_account, db_session, api_settings
    ):
        user = await make_user()
        account = await make_account(user, refresh_token=None, expires_in_seconds=-60)

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "reauth_required"
        # Flagged persistently, so the UI can ask for a reconnect rather than failing
        # every future execution with the same opaque error.
        assert account.needs_reauth is True

    async def test_the_skew_window_refreshes_before_the_token_actually_lapses(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        """A token 30s from expiry must not be handed to a provider: the request would
        outlive it. It is inside the 120s skew, so it takes the refresh path — proven here
        by it demanding a reconnect when no refresh token exists."""
        user = await make_user()
        await make_account(user, refresh_token=None, expires_in_seconds=30)

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "reauth_required"


class TestRefreshing:
    async def test_a_successful_refresh_stores_and_returns_the_new_token(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        user = await make_user()
        account = await make_account(
            user, access_token="ya29.old", expires_in_seconds=-60
        )
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
        _stub_token_endpoint(
            monkeypatch, 200, {"access_token": "ya29.brand-new", "expires_in": 3600}
        )

        token = await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, CALENDAR
        )

        assert token == "ya29.brand-new"
        assert account.needs_reauth is False
        # Re-reading must yield the *new* token, or the next execution refreshes again.
        assert await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, CALENDAR
        ) == "ya29.brand-new"

    async def test_a_refresh_response_without_a_refresh_token_keeps_the_stored_one(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        """Google omits `refresh_token` on refresh. Overwriting the column with null would
        turn a working account into one that can never refresh again — a slow-motion
        version of the bug this whole module exists to fix."""
        user = await make_user()
        account = await make_account(user, expires_in_seconds=-60)
        before = account.refresh_token_enc
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
        _stub_token_endpoint(
            monkeypatch, 200, {"access_token": "ya29.rotated", "expires_in": 3600}
        )

        await OAuthTokenService(db_session, api_settings).fresh_access_token(
            user.id, CALENDAR
        )

        assert account.refresh_token_enc == before

    async def test_a_rejected_refresh_flags_reauth(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        """`invalid_grant` means the user revoked the grant. Retrying cannot fix it."""
        user = await make_user()
        account = await make_account(user, expires_in_seconds=-60)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
        _stub_token_endpoint(monkeypatch, 400, {"error": "invalid_grant"})

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "reauth_required"
        assert account.needs_reauth is True

    async def test_the_providers_own_words_never_reach_the_user(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        """SPEC-010 §3.4's rule, which applies here too: provider bodies echo request
        detail and occasionally credential fragments, so they are logged, never rendered."""
        user = await make_user()
        await make_account(user, expires_in_seconds=-60)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
        _stub_token_endpoint(
            monkeypatch, 400, {"error": "invalid_grant", "secret_echo": "GOCSPX-leaked"}
        )

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert "GOCSPX-leaked" not in excinfo.value.message
        assert "invalid_grant" not in excinfo.value.message

    async def test_an_unreachable_provider_does_not_flag_reauth(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        """A network blip is not a revoked grant. Flagging reauth here would send the user
        to reconnect a perfectly good account, and the next attempt would have worked."""
        import httpx

        user = await make_user()
        account = await make_account(user, expires_in_seconds=-60)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")

        async def _boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(httpx.AsyncClient, "post", _boom)

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "refresh_unreachable"
        assert account.needs_reauth is False

    async def test_a_missing_oauth_app_flags_reauth_rather_than_calling_google(
        self, make_user, make_account, db_session, api_settings, monkeypatch
    ):
        user = await make_user()
        account = await make_account(user, expires_in_seconds=-60)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        with pytest.raises(AppError) as excinfo:
            await OAuthTokenService(db_session, api_settings).fresh_access_token(
                user.id, CALENDAR
            )

        assert excinfo.value.code == "reauth_required"
        assert account.needs_reauth is True


def _stub_token_endpoint(monkeypatch, status: int, body: dict) -> None:
    """Replaces the token POST. Deliberately not a live call: these tests must not depend
    on network access or on a real Google project."""
    import httpx

    async def _post(self, url, **kwargs):  # noqa: ANN001, ANN003
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
