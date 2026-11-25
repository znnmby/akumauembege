"""Pydantic models for authentication inputs, outputs, and token identity."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PositiveInt,
    SecretStr,
    StrictInt,
    field_validator,
    model_validator,
)


EmailField = Annotated[EmailStr, Field(max_length=255)]
RegisterPassword = Annotated[SecretStr, Field(min_length=12, max_length=128)]
LoginPassword = Annotated[SecretStr, Field(min_length=1, max_length=128)]


def normalize_email(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailField
    password: RegisterPassword

    _normalize_email = field_validator("email", mode="before")(normalize_email)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailField
    password: LoginPassword

    _normalize_email = field_validator("email", mode="before")(normalize_email)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: LoginPassword
    new_password: RegisterPassword

    @model_validator(mode="after")
    def ensure_password_changes(self) -> "ChangePasswordRequest":
        if (
            self.current_password.get_secret_value()
            == self.new_password.get_secret_value()
        ):
            raise ValueError("new_password must differ from current_password")
        return self


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    email: EmailStr
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(timezone.utc)


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1)
    token_type: Literal["bearer"] = "bearer"
    expires_in: PositiveInt


class AuthenticatedUser(BaseModel):
    """Internal identity. It must never be used as a public response model."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    session_id: UUID
    email: str
    session_expires_at: datetime

    @field_validator("session_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(timezone.utc)


class JWTClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub: str
    sid: str
    iat: StrictInt
    exp: StrictInt

    @field_validator("sub", "sid")
    @classmethod
    def require_uuid_string(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("claim must be a UUID string")
        parsed = UUID(value)
        if str(parsed) != value.lower():
            raise ValueError("claim must be a canonical UUID string")
        return value

    @field_validator("iat", "exp")
    @classmethod
    def require_positive_numeric_date(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("NumericDate must be positive")
        return value

    @model_validator(mode="after")
    def require_expiry_after_issue(self) -> "JWTClaims":
        if self.exp <= self.iat:
            raise ValueError("exp must be later than iat")
        return self
