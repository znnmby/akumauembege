"""Category catalog persistence and reference-safe deletion."""

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import fail
from app.databases.models import Category, MenuItem
from app.modules.categories.schemas import CategoryCreate, CategoryUpdate


def list_categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category).order_by(Category.id)))


def _locked_category(db: Session, category_id: int) -> Category:
    category = db.scalar(
        select(Category)
        .where(Category.id == category_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if category is None:
        fail(404, "Category not found.", "CATEGORY_NOT_FOUND")
    return category


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "Category name or references conflict.", "CATEGORY_CONFLICT")


def create_category(db: Session, data: CategoryCreate) -> Category:
    category = Category(name=data.name)
    db.add(category)
    _commit(db)
    return category


def update_category(db: Session, category_id: int, data: CategoryUpdate) -> Category:
    category = _locked_category(db, category_id)
    category.name = data.name
    _commit(db)
    return category


def delete_category(db: Session, category_id: int) -> None:
    _locked_category(db, category_id)
    # Soft-deleted menus still belong to their category and preserve order history.
    if db.scalar(select(MenuItem.id).where(MenuItem.category_id == category_id).limit(1)) is not None:
        fail(409, "Category is referenced by menu items.", "CATEGORY_IN_USE")
    try:
        # Avoid ORM relationship synchronization that would null menu foreign keys.
        db.execute(delete(Category).where(Category.id == category_id))
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "Category is referenced by menu items.", "CATEGORY_IN_USE")
