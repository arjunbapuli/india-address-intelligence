"""Structured error responses.

Every error has the same shape:

    {"success": false, "error": {"code": "...", "message": "...", "details": ...}, "request_id": "..."}

Error messages never echo the submitted address back.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

log = get_logger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
        "request_id": request_id_var.get(),
    }


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Pydantic includes the offending input value; drop it so addresses are not echoed.
    clean = []
    for e in errors:
        clean.append({"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")})
    return clean


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details),
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request body or parameters are invalid.",
                               _sanitize_validation_errors(exc.errors())),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", extra={"extra_fields": {"error_type": type(exc).__name__}})
        return JSONResponse(status_code=500, content=error_body("internal_error", "An internal error occurred."))
