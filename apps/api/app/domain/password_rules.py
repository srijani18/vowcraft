"""Password policy — a behavioural port of ``src/lib/password-rules.ts``.

Pure: no hashing, no database, no clock. That separation exists in the original so the
browser can bundle the rules for immediate feedback while the server stays the authority,
and it is worth keeping — the policy is the part with interesting edge cases, and pure
code is the part that is cheap to test exhaustively.

Two hard-won behaviours are preserved deliberately, because the obvious implementation of
each is wrong and was shipped once:

1. **The wordlist matches the whole password, not substrings.** Substring matching
   rejected "a password that should survive", "my welcome home party plans" and "the
   qwerty keyboard is fine" — all strong passphrases — while the genuinely weak cases were
   already caught by the length rule. It punished exactly the people writing good
   passwords.

2. **Identity checks work by proportion, not containment.** The earlier rule rejected any
   password containing the email's local part with a three-character floor, which refused
   "a memorable phrase you will recall" for anyone at ``you@…`` — and equally for
   ``me@``, ``dev@``, ``ops@``, ``sam@``. The intent was never "these letters must not
   appear"; it was "your password must not *be* your address".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200

_COMMON = (
    "password", "letmein", "welcome", "qwerty", "iloveyou", "admin", "monkey",
    "dragon", "football", "baseball", "sunshine", "princess", "trustno1",
    "12345678", "123456789", "1234567890", "qwertyuiop", "abc123",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class FieldError:
    """Carries all three things every caller needs.

    ``field`` so a form can attach the message to an input, ``code`` for the API's stable
    error envelope, ``message`` for the human. One shape rather than two kept in sync.
    """

    field: str
    code: str
    message: str


def _strip(value: str) -> str:
    """Letters and digits only, lowercased, so punctuation cannot disguise a weak word."""
    return _NON_ALNUM.sub("", value.lower())


def is_common_password(password: str) -> bool:
    """True when the password *is* a common one, or one with trivial decoration.

    ``Password1!`` and ``welcome2024`` are the same password as ``password`` and
    ``welcome`` for cracking purposes. ``my welcome home party plans`` is not.
    """
    stripped = _strip(password)
    if not stripped:
        return False

    for common in _COMMON:
        if stripped == common:
            return True
        # Padded only with digits, either side: password12345, welcome2024, 123qwerty.
        # Any run of digits, not a bounded one — five digits instead of two does not make
        # `password` a different password.
        if re.fullmatch(rf"\d*{re.escape(common)}\d*", stripped):
            return True
        # The same word repeated to clear a length minimum.
        if stripped in (common * 2, common * 3):
            return True
        # Or a common word plus a few non-digit characters, so `password!!!!` is caught
        # while a passphrase merely containing the word is not. Three is the threshold:
        # beyond that there is real material in there.
        if stripped.startswith(common) and len(stripped) - len(common) <= 3:
            return True
    return False


def dominates(needle: str, haystack: str) -> bool:
    """True when ``needle`` accounts for most of ``haystack``.

    So the password *is* the name or address rather than merely containing it. "marcus"
    and "marcus2024" are the same password to an attacker who knows the account; "a
    memorable phrase you will recall" is not, though it contains "you".
    """
    if needle not in haystack:
        return False
    # Half or more of the material is the identifier itself.
    if len(needle) * 2 >= len(haystack):
        return True
    # Or everything around it is just digits: marcus1234567.
    return bool(re.fullmatch(r"\d*", haystack.replace(needle, "")))


def validate_email(email: str) -> Optional[FieldError]:
    value = email.strip()
    if not value:
        return FieldError("email", "email_required", "Enter your email address.")
    # Deliberately permissive: the only authority on whether an address exists is sending
    # to it, and an over-strict pattern rejects valid addresses.
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", value):
        return FieldError("email", "email_invalid", "That does not look like an email address.")
    if len(value) > 254:
        return FieldError("email", "email_too_long", "That email address is too long.")
    return None


def validate_name(name: str) -> Optional[FieldError]:
    value = name.strip()
    if not value:
        return FieldError("name", "name_required", "Enter your name.")
    if len(value) < 2:
        return FieldError("name", "name_too_short", "That is too short to be a name.")
    if len(value) > 120:
        return FieldError("name", "name_too_long", "Keep it under 120 characters.")
    return None


def validate_password(
    password: str, *, email: Optional[str] = None, name: Optional[str] = None
) -> Optional[FieldError]:
    """Length first, then variety.

    Not a maze of character-class rules: those push people toward ``Passw0rd!`` while a
    long passphrase scores worse.
    """
    if not password:
        return FieldError("password", "password_required", "Choose a password.")

    if len(password) < MIN_PASSWORD_LENGTH:
        short = MIN_PASSWORD_LENGTH - len(password)
        plural = "" if short == 1 else "s"
        return FieldError(
            "password",
            "password_too_short",
            f"{short} more character{plural} needed — {MIN_PASSWORD_LENGTH} minimum. "
            "A memorable phrase is stronger than a short scramble.",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        return FieldError("password", "password_too_long", "Keep it under 200 characters.")
    if re.fullmatch(r"(.)\1+", password):
        return FieldError("password", "password_repetitive", "That is a single character repeated.")

    stripped = _strip(password)

    local = _strip(email.split("@")[0]) if email and "@" in email else None
    if local and len(local) >= 3 and dominates(local, stripped):
        return FieldError(
            "password",
            "password_contains_email",
            "Your password is essentially your email address. Pick something unrelated to it.",
        )

    stripped_name = _strip(name) if name else None
    if stripped_name and len(stripped_name) >= 4 and dominates(stripped_name, stripped):
        return FieldError(
            "password",
            "password_contains_name",
            "Your password is essentially your name. Pick something unrelated to it.",
        )

    if is_common_password(password):
        return FieldError(
            "password",
            "password_common",
            "That is one of the most commonly used passwords, or a small variation on one. "
            "Pick something less guessable — a few unrelated words works well.",
        )
    return None
