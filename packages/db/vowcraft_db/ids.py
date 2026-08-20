"""Primary-key generation, compatible with Prisma's ``cuid()``.

The live database was created by Prisma, whose ``@default(cuid())`` is generated
*application-side*, not by Postgres. There is therefore no database default to fall back
on: rows inserted by this backend must generate their own ids, in the same shape, or the
table ends up holding two visibly different id formats.

Format (25 chars): ``c`` + timestamp + counter + fingerprint + random, all base36.
Collision resistance comes from the counter (monotonic within a process) combined with
the fingerprint (per-process) and the random block — so two processes inserting in the
same millisecond still differ.
"""

from __future__ import annotations

import os
import secrets
import threading
import time

_BLOCK = 4
_DISCRETE_VALUES = 36**_BLOCK

_counter = 0
_counter_lock = threading.Lock()


def _base36(value: int, pad: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        out = "0"
    else:
        out = ""
        while value:
            value, rem = divmod(value, 36)
            out = digits[rem] + out
    return out[-pad:].rjust(pad, "0")


def _next_counter() -> int:
    global _counter
    with _counter_lock:
        _counter = (_counter + 1) % _DISCRETE_VALUES
        return _counter


def _fingerprint() -> str:
    """Per-process, so two workers inserting in the same millisecond cannot collide."""
    pid = os.getpid() % _DISCRETE_VALUES
    host = sum(ord(c) for c in os.uname().nodename) % _DISCRETE_VALUES
    return _base36(pid, 2) + _base36(host, 2)


_FINGERPRINT = _fingerprint()


def cuid() -> str:
    timestamp = _base36(int(time.time() * 1000), 8)
    counter = _base36(_next_counter(), _BLOCK)
    random_block = _base36(secrets.randbelow(_DISCRETE_VALUES**2), 8)
    return f"c{timestamp}{counter}{_FINGERPRINT}{random_block}"
