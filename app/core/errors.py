"""Backward-compatible exports for application error primitives."""

from app.core.exceptions import (
    AppError,
    ErrorBody,
    ErrorDetail,
    ErrorResponse,
    fail,
    unauthorized,
)

__all__ = [
    "AppError",
    "ErrorBody",
    "ErrorDetail",
    "ErrorResponse",
    "fail",
    "unauthorized",
]
