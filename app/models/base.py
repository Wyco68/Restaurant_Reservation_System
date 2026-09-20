"""Declarative base and shared column types."""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """created_at on every table.

    TIMESTAMPTZ, never naive TIMESTAMP - a booking system that ignores
    timezones is a booking system with a bug.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
