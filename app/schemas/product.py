"""Product schemas (MongoDB).

attributes is an open dict, because products of different categories
have different attributes.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["Starter", "Main", "Dessert", "Drink", "Side"]


class ProductCreate(BaseModel):
    restaurant_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    category: Category
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    currency: str = Field(default="THB", min_length=3, max_length=3)
    is_available: bool = True
    # Any keys are allowed. They are never used for pricing or permissions;
    # only the top-level price is.
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProductOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    restaurant_id: int
    name: str
    description: str | None = None
    category: str
    price: Decimal
    currency: str
    is_available: bool
    attributes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductSummary(BaseModel):
    """Product fields for list views.

    GET /products returns this instead of ProductOut, and the MongoDB query
    only reads these fields.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    restaurant_id: int
    name: str
    category: str
    price: Decimal
    currency: str
    is_available: bool
