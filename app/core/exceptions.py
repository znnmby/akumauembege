"""Domain-safe exceptions and public REST error schemas."""

from __future__ import annotations

from typing import NoReturn

from pydantic import BaseModel, ConfigDict, PositiveInt


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
