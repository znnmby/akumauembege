from __future__ import annotations

from datetime import datetime, timezone
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PositiveInt,
    SecretStr,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID
from app.core.config import get_settings
from app.core.limits import (
    MAX_ASCII_ART_LINES,
    MAX_ASCII_ART_WIDTH,
    MAX_GEOGRAPHIC_DESCRIPTION_LENGTH,
    MAX_MENU_DESCRIPTION_LENGTH,
    MAX_MENU_NAME_LENGTH,
    MAX_REGION_NAME_LENGTH,
    MAX_SELECTION_REASON_LENGTH,
)


def _validate_ascii_art(lines: list[str]) -> list[str]:
    """Validate rectangular printable ASCII without altering any character."""

    if not isinstance(lines, list):
        raise ValueError("ascii_art harus berupa daftar baris")
    if not 1 <= len(lines) <= MAX_ASCII_ART_LINES:
        raise ValueError(f"ascii_art harus memiliki 1 sampai {MAX_ASCII_ART_LINES} baris")

    width: int | None = None
    has_visible_character = False
    for line in lines:
        if not isinstance(line, str):
            raise ValueError("setiap baris ascii_art harus berupa string")
        if not 1 <= len(line) <= MAX_ASCII_ART_WIDTH:
            raise ValueError(f"setiap baris ascii_art harus berisi 1 sampai {MAX_ASCII_ART_WIDTH} karakter")
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in line):
            raise ValueError("ascii_art hanya boleh berisi karakter ASCII printable")
        if width is None:
            width = len(line)
        elif len(line) != width:
            raise ValueError("semua baris ascii_art harus memiliki lebar yang sama")
        has_visible_character = has_visible_character or any(not character.isspace() for character in line)

    if not has_visible_character:
        raise ValueError("ascii_art harus memiliki setidaknya satu karakter non-spasi")
    return lines


class CatalogMenu(BaseModel):
    """One menu exactly as represented by the catalog document."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=MAX_MENU_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_MENU_DESCRIPTION_LENGTH)
    is_active: bool
    ascii_art: list[str]

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text_fields(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("ascii_art")
    @classmethod
    def validate_ascii_art(cls, value: list[str]) -> list[str]:
        return _validate_ascii_art(value)


class CatalogDocument(BaseModel):
    """Root schema for ``data.json``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    menus: list[CatalogMenu] = Field(min_length=1)

    @field_validator("menus", mode="before")
    @classmethod
    def require_json_array(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("menus harus berupa daftar")
        return value

    @model_validator(mode="after")
    def require_unique_ids(self) -> "CatalogDocument":
        menu_ids = [menu.id for menu in self.menus]
        if len(menu_ids) != len(set(menu_ids)):
            raise ValueError("setiap menu katalog harus memiliki id unik")
        return self


class MenuSnapshot(BaseModel):
    """Public and persistent menu shape, deliberately excluding ``is_active``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=MAX_MENU_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_MENU_DESCRIPTION_LENGTH)
    ascii_art: list[str]

    @field_validator("ascii_art")
    @classmethod
    def validate_ascii_art(cls, value: list[str]) -> list[str]:
        return _validate_ascii_art(value)


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


_SCHEMA_LIMITS = get_settings()

RegionName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=2,
        max_length=_SCHEMA_LIMITS.max_region_name_length,
    ),
]
GeographicDescription = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=10,
        max_length=_SCHEMA_LIMITS.max_geographic_description_length,
    ),
]


class RecommendationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_name: RegionName
    geographic_description: GeographicDescription


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    region_name: str
    geographic_description: str
    selection_reason: str
    menu: MenuSnapshot
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(timezone.utc)


Item = TypeVar("Item")


class PaginatedResponse(BaseModel, Generic[Item]):
    model_config = ConfigDict(extra="forbid")

    items: list[Item]
    next_cursor: str | None
    has_next_page: bool


class LLMCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=MAX_MENU_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_MENU_DESCRIPTION_LENGTH)


class LLMSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    region_name: str = Field(min_length=2, max_length=MAX_REGION_NAME_LENGTH)
    geographic_description: str = Field(min_length=10, max_length=MAX_GEOGRAPHIC_DESCRIPTION_LENGTH)
    candidates: list[LLMCandidate] = Field(min_length=1)

    @field_validator("region_name", "geographic_description", mode="before")
    @classmethod
    def trim_context(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("candidates", mode="before")
    @classmethod
    def require_candidate_list(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("candidates harus berupa daftar")
        return value

    @model_validator(mode="after")
    def require_unique_candidate_ids(self) -> "LLMSelectionRequest":
        candidate_ids = [candidate.id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate id harus unik")
        return self


class LLMSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    menu_id: str | None = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    selection_reason: str = Field(min_length=1, max_length=MAX_SELECTION_REASON_LENGTH)

    @field_validator("menu_id")
    @classmethod
    def validate_menu_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not 1 <= len(value) <= 64:
            raise ValueError("menu_id harus berisi 1 sampai 64 karakter")
        if not all(character.isascii() and (character.isalnum() or character in "_-") for character in value):
            raise ValueError("menu_id memiliki format yang tidak valid")
        return value

    @field_validator("selection_reason", mode="before")
    @classmethod
    def trim_selection_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
