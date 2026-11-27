from __future__ import annotations

import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, PositiveInt
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import Any, NoReturn


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    message: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: list[ErrorDetail] | None = None
    retry_after: PositiveInt | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


def error_responses(*statuses: int) -> dict[int, dict[str, Any]]:
    responses: dict[int, dict[str, Any]] = {
        status: {"model": ErrorResponse} for status in statuses
    }
    if 429 in responses:
        responses[429]["headers"] = {
            "Retry-After": {"schema": {"type": "integer", "minimum": 1}}
        }
    if 401 in responses:
        responses[401]["headers"] = {"WWW-Authenticate": {"schema": {"type": "string"}}}
    return responses


class AppError(Exception):
    """An expected error whose public representation contains no internals."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        retry_after: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retry_after = max(1, retry_after) if retry_after is not None else None
        self.headers = dict(headers or {})
        if self.retry_after is not None:
            self.headers["Retry-After"] = str(self.retry_after)

    def response_model(self) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorBody(
                code=self.code,
                message=self.message,
                details=self.details,
                retry_after=self.retry_after,
            )
        )


def unauthorized(message: str = "Autentikasi diperlukan.") -> AppError:
    return AppError(
        status_code=401,
        code="UNAUTHORIZED",
        message=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def fail(status_code: int, code: str, message: str) -> NoReturn:
    raise AppError(status_code=status_code, code=code, message=message)


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
        "Unhandled application error (%s) on %s %s",
        type(exc).__name__,
        request.method,
        getattr(request.scope.get("route"), "path", "<unmatched>"),
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
