"""Public menu queries and role-protected catalog mutations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import DbSession, require_roles
from app.core.schemas import Page
from app.databases.models import User
from app.modules.menu_items import services
from app.modules.menu_items.schemas import (
    MenuAvailabilityUpdate,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
)
from app.modules.realtime.events import broker

router = APIRouter(prefix="/menu-items", tags=["Menu items"])
Admin = Annotated[User, Depends(require_roles("ADMIN"))]
Staff = Annotated[User, Depends(require_roles("CASHIER", "ADMIN"))]
MenuItemId = Annotated[int, Path(gt=0)]


@router.get("", response_model=Page[MenuItemRead])
def list_menu_items(
    db: DbSession,
    category_id: Annotated[int | None, Query(gt=0)] = None,
    q: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return services.list_menu_items(db, category_id, q, page, limit)


@router.get("/{menu_item_id}", response_model=MenuItemRead)
def get_menu_item(menu_item_id: MenuItemId, db: DbSession):
    return services.get_menu_item(db, menu_item_id)


@router.post("", response_model=MenuItemRead, status_code=status.HTTP_201_CREATED)
async def create_menu_item(data: MenuItemCreate, db: DbSession, admin: Admin):
    menu = await run_in_threadpool(services.create_menu_item, db, data)
    response = MenuItemRead.model_validate(menu)
    await broker.publish("menu.created", response.model_dump(mode="json"))
    return response


@router.patch("/{menu_item_id}", response_model=MenuItemRead)
async def update_menu_item(
    menu_item_id: MenuItemId, data: MenuItemUpdate, db: DbSession, admin: Admin
):
    menu = await run_in_threadpool(services.update_menu_item, db, menu_item_id, data)
    response = MenuItemRead.model_validate(menu)
    event = "menu.availability_changed" if "is_available" in data.model_fields_set else "menu.updated"
    await broker.publish(event, response.model_dump(mode="json"))
    return response


@router.patch("/{menu_item_id}/availability", response_model=MenuItemRead)
async def update_availability(
    menu_item_id: MenuItemId, data: MenuAvailabilityUpdate, db: DbSession, staff: Staff
):
    menu = await run_in_threadpool(services.update_availability, db, menu_item_id, data)
    response = MenuItemRead.model_validate(menu)
    await broker.publish("menu.availability_changed", response.model_dump(mode="json"))
    return response


@router.delete("/{menu_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_menu_item(menu_item_id: MenuItemId, db: DbSession, admin: Admin):
    menu = await run_in_threadpool(services.delete_menu_item, db, menu_item_id)
    await broker.publish("menu.deleted", MenuItemRead.model_validate(menu).model_dump(mode="json"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
