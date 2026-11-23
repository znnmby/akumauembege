"""Table structure; initial role and catalog values come from JSON seeds."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.databases.session import Base


class Role(Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    users: Mapped[list[User]] = relationship(back_populates="role_definition")


class OrderStatus(StrEnum):
    QUEUED = "QUEUED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("roles.name"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    role_definition: Mapped[Role] = relationship(back_populates="users")
    customer_orders: Mapped[list[Order]] = relationship(
        back_populates="customer",
        foreign_keys="Order.customer_id",
    )
    handled_orders: Mapped[list[Order]] = relationship(
        back_populates="cashier",
        foreign_keys="Order.cashier_id",
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    menu_items: Mapped[list[MenuItem]] = relationship(back_populates="category")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_available: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    category: Mapped[Category] = relationship(back_populates="menu_items")
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="menu_item")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint(
            "queue_number",
            "queue_date",
            name="uq_orders_queue_number_queue_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    cashier_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    queue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        SqlEnum(
            OrderStatus,
            name="order_status",
            native_enum=False,
            create_constraint=True,
        ),
        default=OrderStatus.QUEUED,
        server_default=OrderStatus.QUEUED.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    customer: Mapped[User] = relationship(
        back_populates="customer_orders",
        foreign_keys=[customer_id],
    )
    cashier: Mapped[User | None] = relationship(
        back_populates="handled_orders",
        foreign_keys=[cashier_id],
    )
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    menu_item: Mapped[MenuItem] = relationship(back_populates="order_items")
