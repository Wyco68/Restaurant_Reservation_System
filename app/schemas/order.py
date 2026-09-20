"""Order schemas.

Note what the client is NOT allowed to send: unit_price and total_amount.
Prices are read from MongoDB server-side. A client-supplied price is a
free-lunch vulnerability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.order import OrderStatus


class OrderItemIn(BaseModel):
    product_id: str = Field(min_length=24, max_length=24, pattern=r"^[0-9a-fA-F]{24}$")
    quantity: int = Field(gt=0, le=50)
    selected_attributes: dict[str, Any] | None = None


class OrderCreate(BaseModel):
    restaurant_id: int = Field(gt=0)
    reservation_id: int | None = Field(default=None, gt=0)
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: str
    product_name: str
    unit_price: Decimal
    quantity: int
    selected_attributes: dict[str, Any] | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: uuid.UUID
    restaurant_id: int
    reservation_id: int | None
    status: OrderStatus
    total_amount: Decimal
    currency: str
    created_at: datetime
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
