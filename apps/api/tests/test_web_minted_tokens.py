"""Tokens minted by the Next.js server — SPEC-015 §7.

A React Server Component runs during SSR with the session cookie, not the browser's
`sessionStorage` where the long-lived bearer-token pair lives, so it cannot call the
backend as the browser does. Instead `src/lib/api-token.ts::mintApiAccessToken` mints its
own short-lived (60s) token, server to server, using the `JWT_SECRET` the two services
share.

The fixture below is a real token captured from that live TypeScript function (100-year
TTL so it never expires during a test run) — not a hand-built payload — so a change to
either side's claim names, claim types, or signing scheme fails here rather than at
render time in production.
"""

from __future__ import annotations

import pytest

from app.core.security import TokenError, decode_token

SECRET = "dev-jwt-secret-change-for-anything-beyond-local-use"
ALGO = "HS256"

# Minted via: node -e "... mintApiAccessToken({id: 'clx0000000000000000000000',
# pwdEpochMs: 1755600000123}, 100 * 365 * 24 * 60 * 60)" against src/lib/api-token.ts.
NODE_MINTED_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJjbHgwMDAwMDAwMDAwMDAwMDAwMDAwMDAwIiwidHlwIjoiYWNjZXNzIiwicHdkIjoxNzU1NjAwMDAwMTIzLCJpYXQiOjE3ODcyMjUxNzcsImV4cCI6NDk0MDgyNTE3N30."
    "GOWot1mPIGgfjG9URYWq9RwwJcCnyrHDHFs-7bHG9C8"
)


class TestNodeMintedTokenDecodesInFastAPI:
    def test_decodes_with_the_shared_secret(self):
        claims = decode_token(NODE_MINTED_TOKEN, secret=SECRET, algorithm=ALGO, expected_type="access")
        assert claims.subject == "clx0000000000000000000000"
        assert claims.token_type == "access"
        assert claims.password_epoch_ms == 1755600000123
        assert claims.issued_at == 1787225177

    def test_rejected_with_the_wrong_secret(self):
        with pytest.raises(TokenError):
            decode_token(NODE_MINTED_TOKEN, secret="wrong-secret", algorithm=ALGO, expected_type="access")

    def test_rejected_as_a_refresh_token(self):
        # A web-minted token is always `typ: access` — it exists for one SSR round trip,
        # never for /auth/refresh.
        with pytest.raises(TokenError):
            decode_token(NODE_MINTED_TOKEN, secret=SECRET, algorithm=ALGO, expected_type="refresh")

    def test_a_tampered_payload_is_rejected(self):
        header, payload, signature = NODE_MINTED_TOKEN.split(".")
        assert payload != ""
        tampered = f"{header}.{payload[:-1]}A.{signature}"
        with pytest.raises(TokenError):
            decode_token(tampered, secret=SECRET, algorithm=ALGO)
