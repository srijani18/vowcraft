"""AES-256-GCM envelope encryption — a byte-exact port of ``src/lib/crypto.ts``.

The database holds ciphertexts written by the Node implementation (BYOK provider keys,
OAuth tokens), so this must interoperate exactly: same key derivation, same IV length,
same tag placement, same base64 dialect, same AAD encoding. A mismatch does not throw at
startup — it throws when a user's stored API key is first read, which is much later and
much harder to diagnose.

Format: ``v1.<iv>.<tag>.<ciphertext>`` — all base64url.

``aad`` binds a ciphertext to its context (e.g. ``cred:userId:service``). A row copied
between users or services then fails to decrypt rather than silently working.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .encoding import b64u_decode, b64u_encode

_VERSION = "v1"
# GCM's standard nonce length. Node uses 12 and the tag is appended separately.
_IV_BYTES = 12


class EncryptionUnavailable(RuntimeError):
    """Raised when no usable key is configured — never with the key in the message."""


class DecryptionFailed(ValueError):
    """Wrong key, tampered ciphertext, or mismatched AAD. Deliberately undifferentiated.

    Distinguishing these for the caller would leak whether a given ciphertext belongs to
    a given context, which is precisely what the AAD binding exists to prevent.
    """


def _key(raw: str | None) -> bytes:
    if not raw:
        raise EncryptionUnavailable(
            "APP_ENCRYPTION_KEY is not set. Secrets cannot be stored. "
            "Generate one with: openssl rand -base64 32"
        )
    try:
        # Standard base64 with padding, matching Node's `Buffer.from(raw, 'base64')`.
        import base64

        buf = base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001 — any decode failure is the same answer
        raise EncryptionUnavailable("APP_ENCRYPTION_KEY is not valid base64") from exc
    if len(buf) != 32:
        raise EncryptionUnavailable(
            f"APP_ENCRYPTION_KEY must decode to 32 bytes, got {len(buf)}"
        )
    return buf


def encryption_available(key_material: str | None) -> bool:
    try:
        _key(key_material)
        return True
    except EncryptionUnavailable:
        return False


def encrypt(plaintext: str, key_material: str | None, aad: str | None = None) -> str:
    iv = os.urandom(_IV_BYTES)
    aesgcm = AESGCM(_key(key_material))
    # `cryptography` returns ciphertext||tag concatenated; Node exposes them separately
    # and the stored format keeps them in separate fields, so split the last 16 bytes.
    sealed = aesgcm.encrypt(iv, plaintext.encode("utf-8"), aad.encode("utf-8") if aad else None)
    ciphertext, tag = sealed[:-16], sealed[-16:]
    return ".".join([_VERSION, b64u_encode(iv), b64u_encode(tag), b64u_encode(ciphertext)])


def decrypt(envelope: str, key_material: str | None, aad: str | None = None) -> str:
    parts = envelope.split(".")
    if len(parts) != 4 or parts[0] != _VERSION:
        raise DecryptionFailed("Stored value is not a recognised encryption envelope.")
    try:
        iv = b64u_decode(parts[1])
        tag = b64u_decode(parts[2])
        ciphertext = b64u_decode(parts[3])
    except (ValueError, TypeError) as exc:
        raise DecryptionFailed("Stored value is not a recognised encryption envelope.") from exc

    aesgcm = AESGCM(_key(key_material))
    try:
        # Re-concatenate for `cryptography`'s combined form.
        plaintext = aesgcm.decrypt(
            iv, ciphertext + tag, aad.encode("utf-8") if aad else None
        )
    except InvalidTag as exc:
        raise DecryptionFailed(
            "Could not decrypt the stored value. The encryption key may have changed."
        ) from exc
    return plaintext.decode("utf-8")


def encrypt_json(value: object, key_material: str | None, aad: str | None = None) -> str:
    import json

    # `separators` matches JSON.stringify's compact output. Not required for
    # correctness — the ciphertext is opaque — but it keeps sizes identical across
    # languages, which makes cross-language fixtures comparable.
    return encrypt(json.dumps(value, separators=(",", ":")), key_material, aad)


def decrypt_json(envelope: str, key_material: str | None, aad: str | None = None):
    import json

    return json.loads(decrypt(envelope, key_material, aad))


# ─────────────────────────────────────────── hashing, for idempotency keys ──


def sha256_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_stringify(value: object) -> str:
    """Deterministic JSON — SPEC-002 §5 step 3.

    Byte-identical to the TypeScript ``stableStringify``, because the output is hashed into
    an idempotency key that must match one written by the other implementation. Two rules
    do the work: **keys sorted**, so ``{a,b}`` and ``{b,a}`` hash the same, and
    ``null`` values **kept**.

    That second point is subtle and got it wrong once. The TypeScript filters ``undefined``,
    not ``null`` — and Python has no ``undefined``, so filtering ``None`` here dropped keys
    the other implementation keeps. The two then produced different keys for the same
    payload, which would let the same action execute twice: for an EMAIL action, the same
    message sent to the same people, and no way to tell from the audit trail that it was a
    duplicate rather than two decisions. Payloads arrive from a JSONB column, where
    ``undefined`` cannot exist, so nothing is filtered.

    Without this, the same payload submitted twice could produce two different keys and
    execute twice — which for an EMAIL action means sending the same message twice.
    """
    import json

    if value is None or not isinstance(value, (dict, list, tuple)):
        # `json.dumps` matches JSON.stringify for scalars, including string escaping.
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(stable_stringify(v) for v in value) + "]"
    entries = sorted(value.items(), key=lambda kv: kv[0])
    body = ",".join(
        f"{json.dumps(k, ensure_ascii=False)}:{stable_stringify(v)}" for k, v in entries
    )
    return "{" + body + "}"


def default_idempotency_key(item_id: str, payload: dict) -> str:
    """The key an execution gets when the caller does not supply one.

    Derived from the item *and* its payload, so editing the payload legitimately produces a
    new key — an edited action is a different action and should be allowed to execute — while
    a resubmitted identical request replays the prior result instead of dispatching again.
    """
    return f"ik_{sha256_hex(f'{item_id}:{stable_stringify(payload)}')[:40]}"
