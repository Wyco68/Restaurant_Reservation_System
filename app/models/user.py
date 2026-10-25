"""Users: login details and roles."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, Enum, Index, String, text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    STAFF = "staff"
    ADMIN = "admin"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    # UUID so user ids cannot be guessed or counted.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # CITEXT makes the email case-insensitive. The unique index gives the
    # 409 on a duplicate signup, even when two signups arrive together.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=UserRole.CUSTOMER,
        server_default=UserRole.CUSTOMER.value,
    )
    # Users are disabled instead of deleted, because orders refer to them.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    restaurants: Mapped[list["Restaurant"]] = relationship(back_populates="owner")  # noqa: F821
    reservations: Mapped[list["Reservation"]] = relationship(back_populates="user")  # noqa: F821
    orders: Mapped[list["Order"]] = relationship(back_populates="user")  # noqa: F821

    __table_args__ = (
        CheckConstraint("char_length(full_name) >= 2", name="ck_users_name_len"),
        Index("idx_users_role", "role"),
    )
