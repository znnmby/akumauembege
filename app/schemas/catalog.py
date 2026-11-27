from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.limits import (
    MAX_MENU_DESCRIPTION_LENGTH,
    MAX_MENU_NAME_LENGTH,
)


class CatalogMenu(BaseModel):
    """One menu exactly as represented by the catalog document."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=MAX_MENU_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_MENU_DESCRIPTION_LENGTH)
    is_active: bool
    your_food: list[str]

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text_fields(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip()


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
    # Read historical snapshots while always serializing the current field name.
    your_food: list[str] = Field(validation_alias=AliasChoices("your_food", "ascii_art"))
