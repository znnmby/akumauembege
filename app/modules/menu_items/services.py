"""Menu filtering, row-locked mutations, and history-preserving deletion."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import fail
from app.core.schemas import paginate
from app.databases.models import Category, MenuItem
from app.modules.menu_items.schemas import (
    MenuAvailabilityUpdate,
    MenuItemCreate,
    MenuItemUpdate,
)


def list_menu_items(
    db: Session,
    category_id: int | None,
    q: str | None,
    page: int,
    limit: int,
) -> dict:
    statement = select(MenuItem).where(MenuItem.deleted_at.is_(None))
    if category_id is not None:
        statement = statement.where(MenuItem.category_id == category_id)
    if q is not None:
        statement = statement.where(MenuItem.name.icontains(q, autoescape=True))
    return paginate(db, statement.order_by(MenuItem.id), page, limit)


def get_menu_item(db: Session, menu_item_id: int) -> MenuItem:
    menu = db.scalar(
        select(MenuItem).where(MenuItem.id == menu_item_id, MenuItem.deleted_at.is_(None))
    )
    if menu is None:
        fail(404, "Menu item not found.", "MENU_ITEM_NOT_FOUND")
    return menu


def _locked_menu_item(db: Session, menu_item_id: int) -> MenuItem:
    menu = db.scalar(
        select(MenuItem)
        .where(MenuItem.id == menu_item_id, MenuItem.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if menu is None:
        fail(404, "Menu item not found.", "MENU_ITEM_NOT_FOUND")
    return menu


def _require_category(db: Session, category_id: int) -> None:
    # The shared row lock prevents category deletion while this menu is assigned.
    category = db.scalar(
        select(Category.id)
        .where(Category.id == category_id)
        .with_for_update(read=True, key_share=True)
    )
    if category is None:
        fail(404, "Category not found.", "CATEGORY_NOT_FOUND")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "Menu item conflicts with existing data.", "MENU_ITEM_CONFLICT")


def create_menu_item(db: Session, data: MenuItemCreate) -> MenuItem:
    _require_category(db, data.category_id)
    menu = MenuItem(**data.model_dump())
    db.add(menu)
    _commit(db)
    return menu


def update_menu_item(db: Session, menu_item_id: int, data: MenuItemUpdate) -> MenuItem:
    menu = _locked_menu_item(db, menu_item_id)
    if "category_id" in data.model_fields_set:
        _require_category(db, data.category_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(menu, field, value)
    _commit(db)
    return menu


def update_availability(
    db: Session, menu_item_id: int, data: MenuAvailabilityUpdate
) -> MenuItem:
    menu = _locked_menu_item(db, menu_item_id)
    menu.is_available = data.is_available
    _commit(db)
    return menu


def delete_menu_item(db: Session, menu_item_id: int) -> MenuItem:
    menu = _locked_menu_item(db, menu_item_id)
    menu.deleted_at = datetime.now(timezone.utc)
    _commit(db)
    return menu
