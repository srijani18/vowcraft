"""Password hashing, envelope encryption, and tokens.

The compatibility claims here were established against the *live* TypeScript
implementation — Python verifying Node-produced values and vice versa — and the fixtures
below are real outputs captured from that run. Keeping them as literals means a change to
either implementation fails here rather than at a user's login screen.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.core.crypto import DecryptionFailed, decrypt, encrypt, encryption_available
from app.core.encoding import b64u_decode, b64u_encode
from app.core.passwords import hash_password, needs_rehash, verify_password
from app.core.security import (
    TokenError,
    claims_match_user,
    decode_token,
    issue_token,
    password_epoch_ms,
)

SECRET = "test-secret-not-used-in-any-deployment"
ALGO = "HS256"


class TestBase64Url:
    def test_matches_node_unpadded_form(self):
        """Node emits RFC 4648 §5 without padding; Python keeps it. Values that look
        right and compare unequal are the failure mode this guards."""
        assert b64u_encode(b"\xff\xfe\xfd") == "__79"
        assert "=" not in b64u_encode(b"a")
        assert b64u_decode("__79") == b"\xff\xfe\xfd"

    def test_round_trips_every_length_remainder(self):
        for size in range(1, 9):
            raw = os.urandom(size)
            assert b64u_decode(b64u_encode(raw)) == raw


class TestPasswords:
    def test_a_hash_verifies(self):
        stored = hash_password("a memorable phrase you will recall")
        assert verify_password("a memorable phrase you will recall", stored)

    def test_a_wrong_password_fails(self):
        stored = hash_password("a memorable phrase you will recall")
        assert not verify_password("something else entirely", stored)

    def test_the_format_is_the_documented_envelope(self):
        parts = hash_password("a memorable phrase you will recall").split(".")
        assert parts[0] == "s1"
        assert len(parts) == 3
        # 16-byte salt, 64-byte key — the values the live database was written with.
        assert len(b64u_decode(parts[1])) == 16
        assert len(b64u_decode(parts[2])) == 64

    @pytest.mark.parametrize(
        "password,stored",
        [
            # Captured from the live TypeScript implementation, verbatim. If Python's KDF
            # ever disagrees with Node's, every existing account is locked out — and this
            # is where that shows up, rather than at someone's login screen.
            ('a memorable phrase you will recall',
             's1.U7HgtVAuN1tALTdcL2hzHg.-AAerax-8yaNDtL0S8AKKrAkpKu6dJo6QBXWUjfIrQs1PxEsfhUmcfqUPvz8L7BYcbD4j9ao4qS8NGrY5E9qJw'),
            ('ünïcödé påsswörd here',
             's1.8eTLA3NhtBEzuOklTv_7Aw.Wlp-aFq-z53tO-QYHgwzrYTnJlTm7_Zb_vElfkuCQA-B7ZnN075AkymC2_Qwjc2-1Ik10wTCKmmSkyqLwolqRw'),
            ('日本語のパスワードです',
             's1.aU3ZB5MZuSbL5IUjaqQ9Zw.YS18rsjqLQsu10wYwreV8b12YYLeK3OIiTS8k29ddFGztIA1yssr5IWxrTggtTwsQ6BSKixzfV98keW023CEYw'),
        ],
    )
    def test_verifies_a_hash_produced_by_the_typescript_implementation(self, password, stored):
        assert verify_password(password, stored)

    def test_unicode_normalises_so_the_same_phrase_matches(self):
        """NFKC before hashing: the same passphrase typed on a different platform can
        arrive in a different normal form and must still verify."""
        composed = "café latte requirements"      # é as one codepoint
        decomposed = "café latte requirements"   # e + combining acute
        assert verify_password(decomposed, hash_password(composed))

    @pytest.mark.parametrize("stored", [None, "", "garbage", "s1.only-two", "s2.a.b", "s1..", "s1.!!.??"])
    def test_a_malformed_hash_returns_false_rather_than_raising(self, stored):
        """A password check must not distinguish "no such user" from "wrong password" by
        raising on one and returning on the other — that difference is an enumerator."""
        assert verify_password("anything", stored) is False

    def test_needs_rehash_tracks_the_version_prefix(self):
        assert needs_rehash("s0.old.hash") is True
        assert needs_rehash(hash_password("a memorable phrase you will recall")) is False
        assert needs_rehash(None) is False


class TestCrypto:
    @pytest.fixture
    def key(self) -> str:
        return base64.b64encode(os.urandom(32)).decode()

    def test_round_trips(self, key):
        assert decrypt(encrypt("gsk_secret", key, "cred:u1:groq"), key, "cred:u1:groq") == "gsk_secret"

    def test_the_format_is_the_documented_envelope(self, key):
        parts = encrypt("x", key).split(".")
        assert parts[0] == "v1"
        assert len(parts) == 4
        assert len(b64u_decode(parts[1])) == 12  # GCM nonce
        assert len(b64u_decode(parts[2])) == 16  # auth tag

    def test_aad_binds_a_ciphertext_to_its_context(self, key):
        """A row copied between users or services must fail rather than silently work."""
        sealed = encrypt("gsk_secret", key, "cred:user_a:groq")
        with pytest.raises(DecryptionFailed):
            decrypt(sealed, key, "cred:user_b:groq")

    def test_a_wrong_key_fails(self, key):
        other = base64.b64encode(os.urandom(32)).decode()
        with pytest.raises(DecryptionFailed):
            decrypt(encrypt("x", key), other)

    def test_tampering_fails(self, key):
        sealed = encrypt("gsk_secret", key, "cred:u1:groq")
        version, iv, tag, ct = sealed.split(".")
        flipped = ct[:-2] + ("AA" if ct[-2:] != "AA" else "AB")
        with pytest.raises(DecryptionFailed):
            decrypt(f"{version}.{iv}.{tag}.{flipped}", key, "cred:u1:groq")

    @pytest.mark.parametrize("plaintext", ["", "ünïcödé påylöad 日本語", "x" * 5000, '{"nested":"json"}'])
    def test_round_trips_awkward_payloads(self, key, plaintext):
        assert decrypt(encrypt(plaintext, key, "aad"), key, "aad") == plaintext

    @pytest.mark.parametrize(
        "plaintext,aad,envelope",
        [
            ('gsk_realProviderKeyShape0123456789', 'cred:user_abc:groq', 'v1.rQxDYfZSP6weNar2.iqUOB5vZVpPIbe8c3A6MTA.xBVK9arCu9qCU43jiMzwwwcFIH-T1-snUad9S1F63apU-A'),
            ('no aad at all', None, 'v1.UMgOByAhVAJu4e0f.92CbBsPF3mXmZcbFlpdaBg.zrbm--CODdtfa-2oTQ'),
        ],
    )
    def test_decrypts_a_ciphertext_produced_by_the_typescript_implementation(
        self, plaintext, aad, envelope
    ):
        """Verbatim Node output, with the key it was sealed under.

        This is the check that stored BYOK keys and OAuth tokens keep working after the
        migration. A regenerated ciphertext would prove only that Python agrees with
        itself.
        """
        assert decrypt(envelope, 'MMHzu0PBaZ4tCkg8wYsIzTjTGJ8oQZmiUsjMbMOWuS0=', aad) == plaintext

    def test_reports_availability_without_revealing_anything(self):
        assert encryption_available(None) is False
        assert encryption_available("too-short") is False
        assert encryption_available(base64.b64encode(os.urandom(32)).decode()) is True


class TestTokens:
    def test_a_token_round_trips(self):
        token = issue_token(subject="u1", token_type="access", password_epoch=123,
                            secret=SECRET, algorithm=ALGO, ttl_seconds=1800)
        claims = decode_token(token, secret=SECRET, algorithm=ALGO, expected_type="access")
        assert claims.subject == "u1"
        assert claims.password_epoch_ms == 123

    def test_millisecond_precision_is_preserved(self):
        """Seconds would leave a hole: a token minted in the same second as a password
        rotation stayed valid, which was a real bug in the original."""
        moment = datetime(2026, 8, 20, 12, 0, 0, 196000, tzinfo=timezone.utc)
        epoch = password_epoch_ms(moment)
        assert str(epoch).endswith("196")

    def test_a_password_change_one_millisecond_later_revokes_the_token(self):
        moment = datetime(2026, 8, 20, 12, 0, 0, 196000, tzinfo=timezone.utc)
        token = issue_token(subject="u1", token_type="access",
                            password_epoch=password_epoch_ms(moment),
                            secret=SECRET, algorithm=ALGO, ttl_seconds=1800)
        claims = decode_token(token, secret=SECRET, algorithm=ALGO)
        assert claims_match_user(claims, moment) is True
        assert claims_match_user(claims, moment + timedelta(milliseconds=1)) is False

    def test_an_account_with_no_password_has_epoch_zero(self):
        """Federated-only sign-in. Zero rather than absent, so the claim is always present
        and always comparable."""
        assert password_epoch_ms(None) == 0

    def test_a_naive_datetime_is_treated_as_UTC(self):
        """Not the system timezone: relying on that would invalidate every outstanding
        token the moment one container's TZ differed from another's."""
        naive = datetime(2026, 8, 20, 12, 0, 0, 196000)
        aware = naive.replace(tzinfo=timezone.utc)
        assert password_epoch_ms(naive) == password_epoch_ms(aware)

    def test_an_access_token_is_refused_where_a_refresh_token_is_required(self):
        """Otherwise the short access lifetime buys nothing."""
        token = issue_token(subject="u1", token_type="access", password_epoch=0,
                            secret=SECRET, algorithm=ALGO, ttl_seconds=1800)
        with pytest.raises(TokenError):
            decode_token(token, secret=SECRET, algorithm=ALGO, expected_type="refresh")

    def test_a_refresh_token_is_refused_where_an_access_token_is_required(self):
        token = issue_token(subject="u1", token_type="refresh", password_epoch=0,
                            secret=SECRET, algorithm=ALGO, ttl_seconds=1800)
        with pytest.raises(TokenError):
            decode_token(token, secret=SECRET, algorithm=ALGO, expected_type="access")

    def test_a_bad_signature_is_refused(self):
        token = issue_token(subject="u1", token_type="access", password_epoch=0,
                            secret=SECRET, algorithm=ALGO, ttl_seconds=1800)
        with pytest.raises(TokenError):
            decode_token(token, secret="a different secret", algorithm=ALGO)

    def test_an_expired_token_is_refused(self):
        token = issue_token(subject="u1", token_type="access", password_epoch=0,
                            secret=SECRET, algorithm=ALGO, ttl_seconds=-10)
        with pytest.raises(TokenError):
            decode_token(token, secret=SECRET, algorithm=ALGO)

    @pytest.mark.parametrize("token", ["", "not.a.token", "a.b", "eyJhbGciOiJub25lIn0..", "x" * 200])
    def test_junk_is_refused_rather_than_crashing(self, token):
        with pytest.raises(TokenError):
            decode_token(token, secret=SECRET, algorithm=ALGO)

    def test_issuing_without_a_secret_fails_loudly(self):
        """Better at the first request than silently issuing unverifiable tokens."""
        with pytest.raises(TokenError):
            issue_token(subject="u1", token_type="access", password_epoch=0,
                        secret="", algorithm=ALGO, ttl_seconds=60)
