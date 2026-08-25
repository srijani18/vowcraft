"""OAuth access-token freshness for the executor — a port of the refresh half of
``src/integrations/oauth.ts`` (SPEC-002 §4).

Only the *read* path lives here. Authorization (the redirect, PKCE state, and the initial
code exchange) is still served by Next.js, because it needs to set cookies and redirect a
browser; this module exists because the thing that actually *executes* actions is this
service, and it previously had no way to refresh an hour-old Google token. The symptom was
sharp: live mode worked for exactly one hour after connecting, then every execution failed
as "not connected" until the user reconnected by hand.

**The AAD is a cross-service contract.** ``persistTokens`` in the TypeScript binds every
token ciphertext with ``f"{user_id}:{provider_id}"`` and no namespace prefix. Both sides
must agree byte for byte or AES-GCM authentication fails — which is exactly the bug that
made a correctly-connected integration report itself unconnected.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from vowcraft_db import IntegrationAccount

from app.core.config import Settings
from app.core.crypto import decrypt, encrypt
from app.core.exceptions import unprocessable
from app.core.logging import logger
from app.db.repositories.users import AuditRepository
from app.integrations.registry import Provider
from app.services.credentials import CredentialService

#: Refresh this far ahead of expiry, so a token cannot lapse mid-request.
_REFRESH_SKEW = timedelta(seconds=120)
_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class OAuthApp:
    client_id: str
    client_secret: str


#: Where each provider's OAuth app credentials come from, mirroring OAUTH_APP_SOURCES in
#: ``src/integrations/oauth.ts``. ``vault_service`` is the credential-vault service id whose
#: ``clientId``/``clientSecret`` fields hold them, when one exists.
_APP_SOURCES: dict[str, dict[str, Optional[str]]] = {
    "google_calendar": {
        "vault_service": "google",
        "client_id_env": "GOOGLE_CLIENT_ID",
        "client_secret_env": "GOOGLE_CLIENT_SECRET",
    },
    "gmail": {
        "vault_service": "google",
        "client_id_env": "GOOGLE_CLIENT_ID",
        "client_secret_env": "GOOGLE_CLIENT_SECRET",
    },
    "notion": {
        "vault_service": "notion_oauth",
        "client_id_env": "NOTION_CLIENT_ID",
        "client_secret_env": "NOTION_CLIENT_SECRET",
    },
    "slack": {
        "vault_service": None,
        "client_id_env": "SLACK_CLIENT_ID",
        "client_secret_env": "SLACK_CLIENT_SECRET",
    },
}

#: Providers whose access tokens actually expire and can be exchanged for a new one.
#: Notion and Slack are deliberately absent: they issue non-expiring tokens and no refresh
#: token at all, so an account of theirs never enters the refresh path below (its
#: ``expires_at`` stays null) and a refresh attempt would be a bug, not a fallback.
_REFRESH_ENDPOINTS: dict[str, str] = {
    "google_calendar": "https://oauth2.googleapis.com/token",
    "gmail": "https://oauth2.googleapis.com/token",
}

#: Serialises concurrent refreshes per account within this process, so two executions
#: firing at once cannot both spend the refresh token. The TypeScript keeps an equivalent
#: in-process map; neither is a distributed lock, and neither needs to be — a duplicate
#: refresh costs a wasted round trip, not correctness.
_locks: dict[str, asyncio.Lock] = {}


def _aad(user_id: str, provider_id: str) -> str:
    """The cross-service binding string. See this module's docstring before changing it."""
    return f"{user_id}:{provider_id}"


def _lock_for(user_id: str, provider_id: str) -> asyncio.Lock:
    key = _aad(user_id, provider_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def resolve_oauth_app(
    credentials: CredentialService, user_id: str, provider_id: str
) -> Optional[OAuthApp]:
    """The provider's client id and secret, vault first then environment — the same
    precedence ``resolveCredential`` applies everywhere else."""
    source = _APP_SOURCES.get(provider_id)
    if source is None:
        return None

    vault_service = source["vault_service"]
    if vault_service:
        try:
            resolved = await credentials.resolve(user_id, vault_service)
            client_id = (resolved.secrets.get("clientId") or "").strip()
            client_secret = (resolved.secrets.get("clientSecret") or "").strip()
            if client_id and client_secret:
                return OAuthApp(client_id, client_secret)
        except Exception:  # noqa: BLE001 — a vault miss must not block the env fallback
            logger.warn("oauth.vault_app_lookup_failed", provider=provider_id)

    import os

    client_id = (os.environ.get(source["client_id_env"] or "") or "").strip()
    client_secret = (os.environ.get(source["client_secret_env"] or "") or "").strip()
    return OAuthApp(client_id, client_secret) if client_id and client_secret else None


class OAuthTokenService:
    def __init__(self, session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditRepository(session)

    async def _mark_needs_reauth(self, account: IntegrationAccount, reason: str) -> None:
        account.needs_reauth = True
        logger.warn(
            "integration.reauth_required",
            provider=account.provider,
            userId=account.user_id,
            reason=reason,
        )
        await self.audit.record(
            event="integration.reauth_required",
            actor_type="SYSTEM",
            actor_id=account.user_id,
            metadata={"provider": account.provider, "reason": reason},
        )
        await self.session.commit()

    async def _persist(self, account: IntegrationAccount, tokens: dict) -> str:
        """Stores a refreshed token set and returns the new access token."""
        bind = _aad(account.user_id, account.provider)
        access_token = tokens["access_token"]
        account.access_token_enc = encrypt(access_token, self.settings.APP_ENCRYPTION_KEY, bind)
        # Google omits refresh_token on a refresh; the stored one must survive that.
        if tokens.get("refresh_token"):
            account.refresh_token_enc = encrypt(
                tokens["refresh_token"], self.settings.APP_ENCRYPTION_KEY, bind
            )
        expires_in = tokens.get("expires_in")
        if expires_in:
            # Naive UTC: `expiresAt` is `timestamp without time zone`, and asyncpg refuses
            # an aware value for it (see vowcraft_db.clock).
            account.expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            ).replace(tzinfo=None)
        if tokens.get("scope"):
            account.scopes = str(tokens["scope"]).split(" ")
        account.needs_reauth = False
        await self.session.commit()
        return access_token

    async def fresh_access_token(self, user_id: str, provider: Provider) -> Optional[str]:
        """A usable access token for this account, refreshing first when it is at or within
        the skew window of expiry.

        Returns ``None`` only when there is nothing connected to work with. A connected
        account that cannot be made usable raises instead, because "not connected" and
        "reconnect me" are different instructions to the person reading them.
        """
        async with _lock_for(user_id, provider.id):
            account = await self.session.scalar(
                select(IntegrationAccount).where(
                    IntegrationAccount.user_id == user_id,
                    IntegrationAccount.provider == provider.id,
                )
            )
            if account is None or not account.access_token_enc:
                return None
            if account.needs_reauth:
                raise unprocessable(
                    "reauth_required",
                    f"{provider.display_name} needs to be reconnected in "
                    "Settings → Integrations.",
                )

            try:
                access_token = decrypt(
                    account.access_token_enc,
                    self.settings.APP_ENCRYPTION_KEY,
                    _aad(user_id, provider.id),
                )
            except Exception:  # noqa: BLE001 — a wrong/rotated key, not a missing account
                logger.error(
                    "execution.token_undecryptable", provider=provider.id, userId=user_id
                )
                raise unprocessable(
                    "token_undecryptable",
                    f"{provider.display_name}'s stored access could not be read. "
                    "APP_ENCRYPTION_KEY may have changed — reconnect it in "
                    "Settings → Integrations.",
                ) from None

            # A null expiry means the provider issues non-expiring tokens (Notion, Slack).
            if account.expires_at is None:
                return access_token

            expires_at = account.expires_at.replace(tzinfo=timezone.utc)
            if expires_at - _REFRESH_SKEW > datetime.now(timezone.utc):
                return access_token

            return await self._refresh(account, provider)

    async def _refresh(self, account: IntegrationAccount, provider: Provider) -> str:
        user_id = account.user_id
        endpoint = _REFRESH_ENDPOINTS.get(provider.id)

        if endpoint is None or not account.refresh_token_enc:
            await self._mark_needs_reauth(
                account,
                "expired_without_refresh_token" if endpoint else "refresh_unsupported",
            )
            raise unprocessable(
                "reauth_required",
                f"{provider.display_name} access expired and cannot be refreshed. "
                "Reconnect it in Settings → Integrations.",
            )

        app = await resolve_oauth_app(
            CredentialService(self.session, self.settings), user_id, provider.id
        )
        if app is None:
            await self._mark_needs_reauth(account, "oauth_app_unconfigured")
            raise unprocessable(
                "reauth_required",
                f"{provider.display_name}'s OAuth app is no longer configured, so its "
                "access cannot be refreshed.",
            )

        try:
            refresh_token = decrypt(
                account.refresh_token_enc,
                self.settings.APP_ENCRYPTION_KEY,
                _aad(user_id, provider.id),
            )
        except Exception:  # noqa: BLE001
            await self._mark_needs_reauth(account, "refresh_token_undecryptable")
            raise unprocessable(
                "reauth_required",
                f"{provider.display_name}'s stored refresh token could not be read. "
                "Reconnect it in Settings → Integrations.",
            ) from None

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    endpoint,
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    data={
                        "refresh_token": refresh_token,
                        "client_id": app.client_id,
                        "client_secret": app.client_secret,
                        "grant_type": "refresh_token",
                    },
                )
        except httpx.HTTPError:
            # A network failure is not a revoked grant: leave the account alone so the next
            # attempt can succeed, rather than sending the user to reconnect for nothing.
            logger.warn("integration.token_refresh_unreachable", provider=provider.id)
            raise unprocessable(
                "refresh_unreachable",
                f"Could not reach {provider.display_name} to refresh access. Try again.",
            ) from None

        if response.status_code >= 400:
            # The body names the reason (invalid_grant for a revoked or expired refresh
            # token) and is logged for the operator, never rendered.
            logger.warn(
                "integration.token_refresh_rejected",
                provider=provider.id,
                status=response.status_code,
                detail=response.text[:500],
            )
            await self._mark_needs_reauth(account, f"refresh_rejected_{response.status_code}")
            raise unprocessable(
                "reauth_required",
                f"Could not refresh {provider.display_name} access. "
                "Reconnect it in Settings → Integrations.",
            )

        token_set = response.json()
        if not token_set.get("access_token"):
            await self._mark_needs_reauth(account, "refresh_returned_no_token")
            raise unprocessable(
                "reauth_required",
                f"{provider.display_name} returned no access token. "
                "Reconnect it in Settings → Integrations.",
            )

        access_token = await self._persist(account, token_set)
        logger.info("integration.token_refreshed", provider=provider.id, userId=user_id)
        return access_token
