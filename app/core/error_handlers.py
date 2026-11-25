"""FastAPI exception handlers with sanitized public responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, ErrorBody, ErrorDetail, ErrorResponse


logger = logging.getLogger(__name__)


def _field_name(location: tuple[Any, ...]) -> str:
    public_parts = [
        str(part) for part in location if part not in {"body", "query", "path"}
    ]
    return ".".join(public_parts) or "request"


def _validation_message(error_type: str) -> str:
    if error_type == "missing":
        return "Field wajib diisi."
    if error_type in {"string_too_short", "too_short"}:
        return "Nilai terlalu pendek."
    if error_type in {"string_too_long", "too_long"}:
        return "Nilai terlalu panjang."
    if error_type == "extra_forbidden":
        return "Field tidak diizinkan."
    if error_type.startswith("string_pattern"):
        return "Format nilai tidak valid."
    return "Nilai tidak valid."


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.response_model().model_dump(exclude_none=True, mode="json"),
        headers=exc.headers,
    )


async def validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        ErrorDetail(
            field=_field_name(tuple(error.get("loc", ()))),
            message=_validation_message(str(error.get("type", ""))),
        )
        # FastAPI stores Pydantic-style mappings but exposes ``errors()``
        # without Pydantic's keyword options. Only location and error type are
        # read; raw messages, input values, and validator context are ignored.
        for error in exc.errors()
    ]
    body = ErrorResponse(
        error=ErrorBody(
            code="VALIDATION_ERROR",
            message="Input tidak valid.",
            details=details,
        )
    )
    return JSONResponse(
        status_code=422,
        content=body.model_dump(exclude_none=True, mode="json"),
    )


async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "UNAUTHORIZED" if exc.status_code == 401 else "HTTP_ERROR"
    message = "Permintaan tidak dapat diproses."
    if isinstance(exc.detail, dict):
        candidate_code = exc.detail.get("code")
        candidate_message = exc.detail.get("message")
        if isinstance(candidate_code, str):
            code = candidate_code
        if isinstance(candidate_message, str):
            message = candidate_message
    elif isinstance(exc.detail, str) and exc.status_code == 404:
        code = "NOT_FOUND"
        message = "Resource tidak ditemukan."
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(
        status_code=exc.status_code,
        content=body.model_dump(exclude_none=True, mode="json"),
        headers=exc.headers,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled application error (%s)",
        type(exc).__name__,
        extra={"method": request.method, "path": request.url.path},
    )
    body = ErrorResponse(
        error=ErrorBody(code="INTERNAL_ERROR", message="Terjadi kesalahan internal.")
    )
    return JSONResponse(
        status_code=500,
        content=body.model_dump(exclude_none=True, mode="json"),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
