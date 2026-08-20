"""One-line structured JSON logs, correlated by request id — SPEC-000 §6.

A port of ``src/lib/logger.ts``, including both of its redaction layers, because the
reason for each still holds:

1. **Field-name denylist** — anything called ``token``, ``secret``, ``apiKey`` etc. is
   replaced wholesale.
2. **Value scrubbing** — key-*shaped* substrings are masked wherever they appear, at any
   nesting depth and inside exception messages. The denylist cannot help when a secret
   arrives inside otherwise innocuous prose: a provider error body echoing the request, a
   URL in a stack frame, a message built by concatenation. Provider keys are long and
   prefixed, which makes them cheap to spot and safe to mask.

Neither layer is the primary defence — secrets are not supposed to reach a log call at
all (SPEC-004 §5) — but the failure they prevent is unrecoverable and the cost is low.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional

_SENSITIVE = re.compile(
    r"(secret|token|password|api[-_]?key|authorization|cookie|refresh|credential|bearer)",
    re.IGNORECASE,
)

# Key-shaped substrings. Nothing else in a log line looks like `gsk_` followed by forty
# base62 characters, so the false-positive risk is negligible and the payoff is total.
_KEY_SHAPED: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(gsk_|sk-proj-|sk-ant-|sk-|csk-|xoxb-|xoxp-|secret_|nvapi-|r8_)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bkey=[A-Za-z0-9._~+/=-]{16,}"),
    # JWTs: three base64url segments. A leaked access token is as dangerous as a key.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)

_MAX_DEPTH = 6


def _scrub(text: str) -> str:
    out = text
    for pattern in _KEY_SHAPED:
        def _mask(match: re.Match[str]) -> str:
            value = match.group(0)
            # Keep the prefix so the line still says *which* provider, never the secret.
            prefix = re.match(r"^(Bearer\s+|key=)", value, re.IGNORECASE)
            if prefix:
                return f"{prefix.group(1)}[redacted]"
            token_prefix = re.match(r"^[A-Za-z_]*[_-]", value)
            return f"{token_prefix.group(0) if token_prefix else ''}[redacted]"

        out = pattern.sub(_mask, out)
    return out


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "[depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, BaseException):
        return {"name": type(value).__name__, "message": _scrub(str(value))}
    if isinstance(value, dict):
        return {
            k: "[redacted]" if _SENSITIVE.search(str(k)) else _redact(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact(v, depth + 1) for v in value]
    return _scrub(str(value))


_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


class Logger:
    """A bound logger. ``child()`` adds context without re-threading it per call."""

    def __init__(self, bound: Optional[dict[str, Any]] = None, threshold: str = "info") -> None:
        self._bound = bound or {}
        self._threshold = _LEVELS.get(threshold, 20)

    def child(self, **extra: Any) -> "Logger":
        merged = {**self._bound, **extra}
        logger = Logger(merged)
        logger._threshold = self._threshold
        return logger

    def _emit(self, level: str, event: str, fields: dict[str, Any]) -> None:
        if _LEVELS[level] < self._threshold:
            return
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            **(_redact({**self._bound, **fields}) or {}),
        }
        stream = sys.stderr if level in ("warn", "error") else sys.stdout
        print(json.dumps(line, default=str), file=stream, flush=True)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit("debug", event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("info", event, fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit("warn", event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("error", event, fields)


logger = Logger({"service": "api"})


def configure_logging(level: str = "info") -> Logger:
    """Set the threshold, and stop uvicorn duplicating every access line.

    Uvicorn's own access log writes an unstructured line for each request, which would
    double the volume and interleave two formats. The structured line from the request
    middleware carries strictly more (request id, duration, outcome).
    """
    global logger
    logger = Logger({"service": "api"})
    logger._threshold = _LEVELS.get(level, 20)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    return logger
