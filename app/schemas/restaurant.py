"""Restaurant schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    cuisine: str = Field(min_length=2, max_length=60)
    city: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=4)
    price_range: int = Field(ge=1, le=4)


class RestaurantSummary(BaseModel):
    """Projection for search results - no address, no owner graph loaded."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cuisine: str
    city: str
    price_range: int
    avg_rating: Decimal | None


class RestaurantOut(RestaurantSummary):
    address: str
    created_at: datetime


class TableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    table_number: str
    capacity: int
    is_active: bool


class AvailabilitySlot(BaseModel):
    restaurant_table_id: int
    table_number: str
    capacity: int
    available: bool
