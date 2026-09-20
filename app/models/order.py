"""orders and order_items - the dual-database seam.

order_items.product_id is a SOFT reference to a MongoDB products._id.
No foreign key is possible across engines, so integrity is maintained by:
  1. validating every product against Mongo BEFORE the PG transaction opens
  2. snapshotting name and price into the row at purchase time
  3. never cascading a Mongo delete into order history
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class OrderStatus(str, enum.Enum):
    PLACED = "placed"
    PREPARING = "preparing"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    restaurant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("restaurants.id", ondelete="RESTRICT"), nullable=False
    )
    # Nullable: a pre-order can exist without a table reservation.
    reservation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=OrderStatus.PLACED,
        server_default=OrderStatus.PLACED.value,
    )
    # NUMERIC, never FLOAT. Binary floating point cannot represent 0.10
    # exactly and the error compounds across line items.
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'THB'")
    )

    user: Mapped["User"] = relationship(back_populates="orders")  # noqa: F821
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="ck_orders_total_nonneg"),
        Index("idx_orders_user", "user_id", text("created_at DESC")),
        Index("idx_orders_restaurant_status", "restaurant_id", "status"),
        Index("idx_orders_reservation", "reservation_id"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    # Soft reference to mongo products._id. 24-char hex ObjectId.
    product_id: Mapped[str] = mapped_column(String(24), nullable=False)
    # Snapshots. A receipt must show what the customer actually paid, even
    # after the menu item is renamed, repriced or removed.
    product_name: Mapped[str] = mapped_column(String(150), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # The dynamic options the customer chose, e.g. {"size": "L", "spice_level": 3}
    selected_attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity <= 50", name="ck_order_items_qty"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_price_nonneg"),
        Index("idx_order_items_order", "order_id"),
        Index("idx_order_items_product", "product_id"),
    )
