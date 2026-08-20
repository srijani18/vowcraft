"""base64url, matching Node's ``Buffer.toString('base64url')``.

Node emits unpadded RFC 4648 §5 (``-`` and ``_``, no ``=``). Python's
``urlsafe_b64encode`` uses the same alphabet but *keeps* padding, so the padding has to
be stripped on the way out and restored on the way in. Getting this wrong produces
values that look right and compare unequal.
"""

from __future__ import annotations

import base64


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    # Restore the padding Node omitted: base64 decodes in 4-character groups.
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
