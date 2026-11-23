"""Order input constraints and detached, history-preserving responses."""

from datetime import date, datetime
from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field, field_validator

from app.databases.models import OrderStatus


class OrderItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1000)


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemCreate] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def unique_menu_items(cls, items: list[OrderItemCreate]) -> list[OrderItemCreate]:
        ids = [item.menu_item_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each menu_item_id may occur only once.")
        return items


class OrderComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: Literal["CASH", "QRIS", "CARD"] = "CASH"


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    menu_item_id: int
    name: str = Field(validation_alias=AliasPath("menu_item", "name"))
    quantity: int


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    cashier_id: int | None
    queue_number: int | None
    queue_date: date | None
    status: OrderStatus
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    paid_at: datetime | None
    payment_method: str | None
    items: list[OrderItemResponse]
