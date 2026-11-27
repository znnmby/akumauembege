from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.schemas.catalog import MenuSnapshot


Item = TypeVar("Item")


class PaginatedResponse(BaseModel, Generic[Item]):
    model_config = ConfigDict(extra="forbid")

    items: list[Item]
    next_cursor: str | None
    has_next_page: bool


class MenuPageResponse(PaginatedResponse[MenuSnapshot]):
    previous_cursor: str | None
    has_previous_page: bool
