"""Reservations table.

Used by the CP2 name migration (guest_name to first_name / last_name) and by
the concurrency work: the no_double_booking constraint for new bookings and
the xmin version check for edits.
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
    FetchedValue,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from app.models.base import Base, TimestampMixin


class Xid(UserDefinedType):
    """PostgreSQL's xid type, used for the xmin version column.

    With a plain String the version is sent as VARCHAR, and PostgreSQL
    cannot compare xid with varchar.
    """

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "xid"


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

    # Name columns for the migration. guest_name is the old column (nullable
    # in 0003, dropped in 0004). first_name and last_name are added in 0002
    # as nullable with no default, so adding them does not rewrite the table.
    #
    # Each column only exists during part of the migration, so:
    #   deferred        a normal SELECT leaves them out; the service loads
    #                   the ones the current phase reads
    #   raiseload       reading one that was not loaded raises an error
    #   FetchedValue()  an unset column is left out of the INSERT
    guest_name: Mapped[str | None] = mapped_column(
        String(120), server_default=FetchedValue(),
        deferred=True, deferred_raiseload=True,
    )
    first_name: Mapped[str | None] = mapped_column(
        String(60), server_default=FetchedValue(),
        deferred=True, deferred_raiseload=True,
    )
    last_name: Mapped[str | None] = mapped_column(
        String(60), server_default=FetchedValue(),
        deferred=True, deferred_raiseload=True,
    )

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

    # Version for optimistic locking. xmin is a PostgreSQL system column that
    # changes on every update of the row, so we do not need our own counter
    # or a migration. With version_id_col every ORM update becomes
    #     UPDATE reservations SET ... WHERE id = :id AND xmin = :version
    # If another transaction changed the row first, 0 rows match and
    # SQLAlchemy raises StaleDataError (the API returns 409).
    version: Mapped[str] = mapped_column(
        "xmin", Xid, system=True, server_default=FetchedValue()
    )

    user: Mapped["User"] = relationship(back_populates="reservations")  # noqa: F821

    __mapper_args__ = {
        # Otherwise SQLAlchemy would return the name columns after INSERT,
        # and they may not exist yet.
        "eager_defaults": False,
        "version_id_col": version,
        # PostgreSQL sets the new xmin itself.
        "version_id_generator": False,
    }

    __table_args__ = (
        CheckConstraint("party_size > 0 AND party_size <= 20", name="ck_reservations_party"),
        CheckConstraint("ends_at > starts_at", name="ck_reservations_time_order"),
        Index("idx_reservations_user", "user_id", text("starts_at DESC")),
        Index("idx_reservations_restaurant_time", "restaurant_id", "starts_at"),
    )
    # The no_double_booking EXCLUDE constraint is created in migration 0001
    # with plain SQL, which is easier to read than ExcludeConstraint.
