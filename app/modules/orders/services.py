"""Database-backed order lifecycle and durable, daily UTC queue allocation."""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import fail
from app.core.schemas import paginate
from app.databases.models import MenuItem, Order, OrderItem, OrderStatus, QueueCounter, User
from app.modules.orders.schemas import OrderComplete, OrderCreate, OrderResponse


_ORDER_DETAIL = selectinload(Order.items).selectinload(OrderItem.menu_item)


def _require_roles(user: User, *roles: str) -> None:
    if user.role not in roles:
        fail(403, "This role cannot perform this order action.", "FORBIDDEN")


def _require_owner(order: Order, user: User) -> None:
    if order.customer_id != user.id:
        fail(403, "This order belongs to another customer.", "FORBIDDEN")


def _load_order(db: Session, order_id: int, *, lock: bool = False) -> Order:
    statement = (
        select(Order)
        .where(Order.id == order_id)
        .options(_ORDER_DETAIL)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    order = db.scalar(statement)
    if order is None:
        fail(404, "Order not found.", "ORDER_NOT_FOUND")
    return order


def _lock_available_menus(db: Session, menu_ids: list[int]) -> dict[int, MenuItem]:
    menus = list(
        db.scalars(
            select(MenuItem)
            .where(MenuItem.id.in_(menu_ids))
            .order_by(MenuItem.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if len(menus) != len(menu_ids):
        fail(404, "One or more menu items do not exist.", "MENU_ITEM_NOT_FOUND")
    if any(menu.deleted_at is not None or not menu.is_available for menu in menus):
        fail(409, "One or more menu items are unavailable.", "MENU_ITEM_UNAVAILABLE")
    return {menu.id: menu for menu in menus}


def _save_order(db: Session, order: Order) -> OrderResponse:
    try:
        db.flush()
        # Materialize all response data before commit expires ORM attributes.
        result = OrderResponse.model_validate(_load_order(db, order.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, "The order conflicts with an existing record.", "ORDER_CONFLICT")
    return result


def _next_queue_number(db: Session, queue_date: date) -> int:
    existing_max = (
        select(func.coalesce(func.max(Order.queue_number), 0))
        .where(Order.queue_date == queue_date)
        .scalar_subquery()
    )
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(QueueCounter).values(
            queue_date=queue_date, last_number=existing_max + 1
        )
        next_number = func.greatest(QueueCounter.last_number, existing_max) + 1
    elif dialect == "sqlite":
        statement = sqlite_insert(QueueCounter).values(
            queue_date=queue_date, last_number=existing_max + 1
        )
        next_number = func.max(QueueCounter.last_number, existing_max) + 1
    else:
        raise RuntimeError("Durable order queues require PostgreSQL or SQLite.")
    statement = statement.on_conflict_do_update(
        index_elements=[QueueCounter.queue_date],
        set_={"last_number": next_number},
    ).returning(QueueCounter.last_number)
    try:
        return db.execute(statement).scalar_one()
    except IntegrityError:
        db.rollback()
        fail(409, "The queue allocation conflicts with an existing record.", "QUEUE_CONFLICT")


def create_order(db: Session, user: User, payload: OrderCreate) -> OrderResponse:
    _require_roles(user, "CUSTOMER")
    menus = _lock_available_menus(db, [item.menu_item_id for item in payload.items])
    order = Order(
        customer_id=user.id,
        status=OrderStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        items=[
            OrderItem(menu_item=menus[item.menu_item_id], quantity=item.quantity)
            for item in payload.items
        ],
    )
    db.add(order)
    return _save_order(db, order)


def confirm_order(db: Session, user: User, order_id: int) -> OrderResponse:
    _require_roles(user, "CUSTOMER")
    order = _load_order(db, order_id, lock=True)
    _require_owner(order, user)
    if order.status != OrderStatus.PENDING:
        fail(409, "Only a pending order can be confirmed.", "INVALID_ORDER_STATE")
    _lock_available_menus(db, [item.menu_item_id for item in order.items])
    queue_date = datetime.now(timezone.utc).date()
    order.queue_number = _next_queue_number(db, queue_date)
    order.queue_date = queue_date
    order.status = OrderStatus.QUEUED
    return _save_order(db, order)


def list_orders(
    db: Session,
    user: User,
    status: OrderStatus | None,
    created_date: date | None,
    page: int,
    limit: int,
) -> dict:
    """Filter dates by created_at in [00:00 UTC, next 00:00 UTC), not queue_date."""
    _require_roles(user, "CUSTOMER", "CASHIER", "ADMIN")
    statement = select(Order).options(_ORDER_DETAIL).order_by(Order.id)
    if user.role == "CUSTOMER":
        statement = statement.where(Order.customer_id == user.id)
    if status is not None:
        statement = statement.where(Order.status == status)
    if created_date is not None:
        start = datetime.combine(created_date, time.min, tzinfo=timezone.utc)
        statement = statement.where(Order.created_at >= start, Order.created_at < start + timedelta(days=1))
    result = paginate(db, statement, page, limit)
    result["items"] = [OrderResponse.model_validate(order) for order in result["items"]]
    return result


def get_order(db: Session, user: User, order_id: int) -> OrderResponse:
    _require_roles(user, "CUSTOMER", "CASHIER", "ADMIN")
    order = _load_order(db, order_id)
    if user.role == "CUSTOMER":
        _require_owner(order, user)
    return OrderResponse.model_validate(order)


def complete_order(
    db: Session, user: User, order_id: int, payload: OrderComplete
) -> OrderResponse:
    _require_roles(user, "CASHIER")
    order = _load_order(db, order_id, lock=True)
    if order.status != OrderStatus.QUEUED:
        fail(409, "Only a queued order can be completed.", "INVALID_ORDER_STATE")
    now = datetime.now(timezone.utc)
    order.status = OrderStatus.COMPLETED
    order.cashier_id = user.id
    order.completed_at = now
    order.paid_at = now
    order.payment_method = payload.payment_method
    return _save_order(db, order)


def cancel_order(db: Session, user: User, order_id: int) -> OrderResponse:
    _require_roles(user, "CUSTOMER", "CASHIER")
    order = _load_order(db, order_id, lock=True)
    if user.role == "CUSTOMER":
        _require_owner(order, user)
    if order.status not in (OrderStatus.PENDING, OrderStatus.QUEUED):
        fail(409, "Only a pending or queued order can be cancelled.", "INVALID_ORDER_STATE")
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = datetime.now(timezone.utc)
    if user.role == "CASHIER":
        order.cashier_id = user.id
    return _save_order(db, order)
