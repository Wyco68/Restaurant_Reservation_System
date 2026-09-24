"""reservations - the transactional heart of the system.

This table carries both hard problems:
  * zero-downtime migration: guest_name -> first_name / last_name
  * concurrency control:     the no_double_booking EXCLUDE constraint
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ReservationStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Reservation(Base, TimestampMixin):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    restaurant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    restaurant_table_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("restaurant_tables.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # --- Migration columns -----------------------------------------------
    # guest_name is the LEGACY field, dropped at the Contract step.
    guest_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # first_name / last_name are added by the Expand step. Nullable with no
    # default ON PURPOSE: in PostgreSQL 11+ that is a metadata-only change,
    # so there is no table rewrite and no long ACCESS EXCLUSIVE lock. A
    # NOT NULL DEFAULT would rewrite the table and defeat the exercise.
    first_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(60), nullable=True)

    party_size: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(
            ReservationStatus,
            name="reservation_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ReservationStatus.PENDING,
        server_default=ReservationStatus.PENDING.value,
    )

    user: Mapped["User"] = relationship(back_populates="reservations")  # noqa: F821

    __table_args__ = (
        CheckConstraint("party_size > 0 AND party_size <= 20", name="ck_reservations_party"),
        CheckConstraint("ends_at > starts_at", name="ck_reservations_time_order"),
        Index("idx_reservations_user", "user_id", text("starts_at DESC")),
        Index("idx_reservations_restaurant_time", "restaurant_id", "starts_at"),
    )
    # The no_double_booking EXCLUDE constraint is created in migration 0001 via
    # op.execute(). SQLAlchemy has ExcludeConstraint, but writing it as explicit
    # DDL in the migration keeps the single most important line of the schema
    # readable to anyone auditing it.
