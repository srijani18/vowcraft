"""Application errors, and the single envelope every failure is rendered into.

Mirrors ``src/lib/errors.ts``: the wire shape is ``{"error": {"code", "message",
"details"?}}``, and the frontend already reads exactly that. Keeping it identical is what
lets the frontend be pointed at this service without touching its error handling.

``message`` is written for a user. It never interpolates a provider response body, a
stack, or an internal identifier — the same rule the extraction layer follows
(SPEC-010 §3.4), applied at the boundary so no route can opt out of it.
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """A failure with an intended HTTP status and a user-facing sentence."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details

    def to_envelope(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


# ── constructors, so call sites read as prose and statuses stay consistent ──


def bad_request(code: str, message: str, details: Optional[Any] = None) -> AppError:
    return AppError(400, code, message, details)


def unauthorized(message: str = "You are not signed in.") -> AppError:
    return AppError(401, "unauthenticated", message)


def forbidden(message: str = "You do not have access to that.") -> AppError:
    return AppError(403, "forbidden", message)


def not_found(message: str = "That does not exist.") -> AppError:
    """404 for anything the caller does not own, too.

    Answering 403 for another user's row confirms it exists, which turns any id-guessing
    loop into an enumeration oracle. Absent and not-yours look identical from outside.
    """
    return AppError(404, "not_found", message)


def conflict(code: str, message: str, details: Optional[Any] = None) -> AppError:
    return AppError(409, code, message, details)


def unprocessable(code: str, message: str, details: Optional[Any] = None) -> AppError:
    return AppError(422, code, message, details)


def bad_gateway(code: str, message: str, details: Optional[Any] = None) -> AppError:
    return AppError(502, code, message, details)


def service_unavailable(code: str, message: str, details: Optional[Any] = None) -> AppError:
    return AppError(503, code, message, details)
