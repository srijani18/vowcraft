"""Exception handlers: every failure leaves as the documented envelope.

Registered centrally so no route can answer with a different shape, and so an unhandled
exception cannot leak a stack trace or an internal message to a client. The frontend reads
``{"error": {"code", "message"}}`` and nothing else.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.logging import logger


def _envelope(status: int, code: str, message: str, request_id: str, details=None) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers={"x-request-id": request_id})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        # 5xx is ours to explain; 4xx is the caller's to fix. Only the former is an error
        # in the log, so alerting on log level stays meaningful.
        emit = logger.error if exc.status_code >= 500 else logger.warn
        emit("request.failed", requestId=rid, code=exc.code, status=exc.status_code,
             path=request.url.path)
        return _envelope(exc.status_code, exc.code, exc.message, rid, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        # Field paths and messages, not Pydantic's full error objects: those carry the
        # submitted input back, which for a login route means the password.
        details = [
            {"path": ".".join(str(p) for p in err.get("loc", ())[1:]), "message": err.get("msg", "")}
            for err in exc.errors()
        ][:12]
        logger.warn("request.invalid", requestId=rid, path=request.url.path, issues=details)
        return _envelope(400, "invalid_request", "Request validation failed.", rid, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _envelope(exc.status_code, code, message, rid)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        # The exception goes to the log; the client gets a sentence. A stack trace in a
        # response body is a gift to anyone probing the service.
        logger.error("request.unhandled", requestId=rid, path=request.url.path, err=exc)
        return _envelope(500, "internal_error", "Something went wrong. Please try again.", rid)
