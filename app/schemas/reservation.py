"""Reservation schemas.

ReservationOut keeps the same shape during the name migration, even when
the API switches which columns it reads (see to_out in
app/services/reservations.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.reservation import ReservationStatus


class ReservationCreate(BaseModel):
    restaurant_id: int = Field(gt=0)
    restaurant_table_id: int = Field(gt=0)
    guest_name: str = Field(min_length=2, max_length=120)
    party_size: int = Field(gt=0, le=20)
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def must_be_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must include a timezone offset")
        return v

    @model_validator(mode="after")
    def check_window(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if (self.ends_at - self.starts_at).total_seconds() > 6 * 3600:
            raise ValueError("reservation cannot exceed 6 hours")
        return self


class ReservationUpdate(BaseModel):
    guest_name: str | None = Field(default=None, min_length=2, max_length=120)
    party_size: int | None = Field(default=None, gt=0, le=20)
    status: ReservationStatus | None = None


class ReservationOut(BaseModel):
    id: int
    user_id: uuid.UUID
    restaurant_id: int
    restaurant_table_id: int
    guest_name: str
    party_size: int
    starts_at: datetime
    ends_at: datetime
    status: ReservationStatus
    created_at: datetime
    # Version for optimistic locking, also sent as the ETag header. Send it
    # back in If-Match to PATCH.
    version: int


class ReservationListItem(ReservationOut):
    """One row of GET /reservations, with the restaurant name joined in."""

    restaurant_name: str
