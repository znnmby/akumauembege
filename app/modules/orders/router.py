"""Order endpoints; notifications are published only after successful commits."""

from datetime import date as CalendarDate
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import DbSession, require_roles
from app.core.schemas import Page
from app.databases.models import OrderStatus, User
from app.modules.orders import services
from app.modules.orders.schemas import OrderComplete, OrderCreate, OrderResponse
from app.modules.realtime.events import broker


router = APIRouter(prefix="/orders", tags=["orders"])
Customer = Annotated[User, Depends(require_roles("CUSTOMER"))]
OrderReader = Annotated[User, Depends(require_roles("CUSTOMER", "CASHIER", "ADMIN"))]
Cashier = Annotated[User, Depends(require_roles("CASHIER"))]
OrderCanceller = Annotated[User, Depends(require_roles("CUSTOMER", "CASHIER"))]
OrderId = Annotated[int, Path(gt=0)]


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(payload: OrderCreate, db: DbSession, user: Customer) -> OrderResponse:
    order = await run_in_threadpool(services.create_order, db, user, payload)
    await broker.publish("order.created", order.model_dump(mode="json"), customer_id=order.customer_id)
    return order


@router.post("/{order_id}/confirm", response_model=OrderResponse)
async def confirm_order(order_id: OrderId, db: DbSession, user: Customer) -> OrderResponse:
    order = await run_in_threadpool(services.confirm_order, db, user, order_id)
    await broker.publish("order.queued", order.model_dump(mode="json"), customer_id=order.customer_id)
    return order


@router.get("", response_model=Page[OrderResponse])
def list_orders(
    db: DbSession,
    user: OrderReader,
    status: Annotated[OrderStatus | None, Query()] = None,
    date: Annotated[
        CalendarDate | None,
        Query(description="Filter by order created_at date in UTC, including pending orders."),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return services.list_orders(db, user, status, date, page, limit)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: OrderId, db: DbSession, user: OrderReader) -> OrderResponse:
    return services.get_order(db, user, order_id)


@router.patch("/{order_id}/complete", response_model=OrderResponse)
async def complete_order(
    order_id: OrderId,
    db: DbSession,
    user: Cashier,
    payload: Annotated[OrderComplete | None, Body()] = None,
) -> OrderResponse:
    order = await run_in_threadpool(
        services.complete_order, db, user, order_id, payload or OrderComplete()
    )
    await broker.publish("order.completed", order.model_dump(mode="json"), customer_id=order.customer_id)
    return order


@router.patch("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(order_id: OrderId, db: DbSession, user: OrderCanceller) -> OrderResponse:
    order = await run_in_threadpool(services.cancel_order, db, user, order_id)
    await broker.publish("order.cancelled", order.model_dump(mode="json"), customer_id=order.customer_id)
    return order
