"""Password hashing — a byte-exact port of ``src/lib/password.ts``.

Every constant here is load-bearing. The database holds hashes produced by the Node
implementation, so a value that differs in salt length, key length, scrypt parameters,
Unicode normalisation or base64 dialect does not merely differ — it locks every existing
user out of their account with a correct password.

``hashlib.scrypt`` is deliberately not used: it is absent when Python is linked against
LibreSSL (macOS system Python, among others), so the KDF comes from ``cryptography``,
which is already a dependency and available everywhere.

Format: ``s1.<salt-b64url>.<hash-b64url>``
"""

from __future__ import annotations

import hmac
import os
import unicodedata

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .encoding import b64u_decode, b64u_encode

# N=2^15 keeps a single hash around 100ms on modest hardware — slow enough to make
# offline cracking expensive, fast enough not to hold a request open. Matches
# password.ts PARAMS exactly; `maxmem` has no Python equivalent and does not affect
# the output (it is a Node-side allocation guard, not a KDF parameter).
_N = 32_768
_R = 8
_P = 1
_KEYLEN = 64
_SALT_BYTES = 16
_VERSION = "s1"


def _derive(password: str, salt: bytes) -> bytes:
    # NFKC before encoding, as the Node side does: the same passphrase typed on a
    # different keyboard or platform can arrive in a different normal form, and
    # without this it would hash differently and fail to log in.
    normalised = unicodedata.normalize("NFKC", password).encode("utf-8")
    return Scrypt(salt=salt, length=_KEYLEN, n=_N, r=_R, p=_P).derive(normalised)


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    return f"{_VERSION}.{b64u_encode(salt)}.{b64u_encode(_derive(password, salt))}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time comparison. False for a malformed or absent hash, never an error.

    A password check must not distinguish "no such user" from "wrong password" by
    raising on one and returning on the other — that difference is an account
    enumerator.
    """
    if not stored:
        return False
    parts = stored.split(".")
    if len(parts) != 3 or parts[0] != _VERSION:
        return False
    try:
        salt = b64u_decode(parts[1])
        expected = b64u_decode(parts[2])
    except (ValueError, TypeError):
        return False
    if len(expected) != _KEYLEN:
        return False
    return hmac.compare_digest(_derive(password, salt), expected)


def needs_rehash(stored: str | None) -> bool:
    """Whether a stored hash predates the current cost factor.

    The version prefix exists so the cost can be raised later: on a successful login
    against an older version, the caller re-hashes with the current parameters.
    """
    if not stored:
        return False
    return not stored.startswith(f"{_VERSION}.")
