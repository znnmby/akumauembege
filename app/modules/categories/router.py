"""Public category reads and administrator-only mutations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import DbSession, require_roles
from app.databases.models import User
from app.modules.categories import services
from app.modules.categories.schemas import CategoryCreate, CategoryRead, CategoryUpdate
from app.modules.realtime.events import broker

router = APIRouter(prefix="/categories", tags=["Categories"])
Admin = Annotated[User, Depends(require_roles("ADMIN"))]
CategoryId = Annotated[int, Path(gt=0)]


@router.get("", response_model=list[CategoryRead])
def list_categories(db: DbSession):
    return services.list_categories(db)


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(data: CategoryCreate, db: DbSession, admin: Admin):
    category = await run_in_threadpool(services.create_category, db, data)
    response = CategoryRead.model_validate(category)
    await broker.publish("category.created", response.model_dump(mode="json"))
    return response


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: CategoryId, data: CategoryUpdate, db: DbSession, admin: Admin
):
    category = await run_in_threadpool(services.update_category, db, category_id, data)
    response = CategoryRead.model_validate(category)
    await broker.publish("category.updated", response.model_dump(mode="json"))
    return response


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: CategoryId, db: DbSession, admin: Admin):
    await run_in_threadpool(services.delete_category, db, category_id)
    await broker.publish("category.deleted", {"id": category_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
