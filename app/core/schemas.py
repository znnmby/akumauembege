"""Shared response models and deterministic SQL pagination."""

from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    limit: int
    pages: int


def paginate(db: Session, statement, page: int, limit: int) -> dict:
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = list(db.scalars(statement.offset((page - 1) * limit).limit(limit)).unique())
    return {"items": items, "total": total, "page": page, "limit": limit, "pages": ceil(total / limit)}
