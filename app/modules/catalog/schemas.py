"""Pydantic models for the file-backed menu catalog."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_ascii_art(lines: list[str]) -> list[str]:
    """Validate rectangular printable ASCII without altering any character."""

    if not isinstance(lines, list):
        raise ValueError("ascii_art harus berupa daftar baris")
    if not 1 <= len(lines) <= 100:
        raise ValueError("ascii_art harus memiliki 1 sampai 100 baris")

    width: int | None = None
    has_visible_character = False
    for line in lines:
        if not isinstance(line, str):
            raise ValueError("setiap baris ascii_art harus berupa string")
        if not 1 <= len(line) <= 200:
            raise ValueError("setiap baris ascii_art harus berisi 1 sampai 200 karakter")
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
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=2000)
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
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=2000)
    ascii_art: list[str]

    @field_validator("ascii_art")
    @classmethod
    def validate_ascii_art(cls, value: list[str]) -> list[str]:
        return _validate_ascii_art(value)
