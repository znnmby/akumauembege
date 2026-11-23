"""Menu item request and response models without pricing fields."""

from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

MenuName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
]
CategoryId = Annotated[int, Field(strict=True, gt=0)]


class MenuItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: MenuName
    category_id: CategoryId
    is_available: StrictBool = True


class MenuItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: MenuName | None = None
    category_id: CategoryId | None = None
    is_available: StrictBool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> "MenuItemUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one menu field must be provided.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Menu fields cannot be null.")
        return self


class MenuAvailabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_available: StrictBool


class MenuItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    name: str
    is_available: bool
    created_at: datetime
    deleted_at: datetime | None
