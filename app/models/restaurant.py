"""restaurants and restaurant_tables."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Restaurant(Base, TimestampMixin):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    cuisine: Mapped[str] = mapped_column(String(60), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    price_range: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Denormalized rollup of the MongoDB reviews collection. NOT a source of
    # truth - it exists so restaurant search can sort by rating without a
    # per-row cross-engine call (an N+1 across two databases).
    avg_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)

    owner: Mapped["User"] = relationship(back_populates="restaurants")  # noqa: F821
    tables: Mapped[list["RestaurantTable"]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("price_range BETWEEN 1 AND 4", name="ck_restaurants_price_range"),
        CheckConstraint(
            "avg_rating IS NULL OR (avg_rating >= 1.0 AND avg_rating <= 5.0)",
            name="ck_restaurants_avg_rating",
        ),
        Index("idx_restaurants_cuisine_city", "cuisine", "city"),
        Index("idx_restaurants_owner", "owner_id"),
        Index("idx_restaurants_rating", text("avg_rating DESC NULLS LAST")),
    )


class RestaurantTable(Base):
    """The scarce resource. Everything about concurrency control orbits this."""

    __tablename__ = "restaurant_tables"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    restaurant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    table_number: Mapped[str] = mapped_column(String(10), nullable=False)
    capacity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    restaurant: Mapped["Restaurant"] = relationship(back_populates="tables")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "table_number", name="uq_tables_restaurant_number"),
        CheckConstraint("capacity > 0 AND capacity <= 20", name="ck_tables_capacity"),
        Index("idx_tables_restaurant", "restaurant_id"),
    )
