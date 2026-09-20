"""Products router (MongoDB).

CP1 §3 required endpoints:
    GET  /api/v1/products   -> 200, paginated catalogue
    POST /api/v1/products   -> 201, dynamic attributes
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import mongo
from app.models.user import UserRole
from app.schemas.common import Page
from app.schemas.product import ProductCreate, ProductOut, ProductSummary
from app.security import require_role

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _serialize(doc: dict[str, Any]) -> dict[str, Any]:
    doc["_id"] = str(doc["_id"])
    return doc


@router.get("", response_model=Page[ProductSummary])
async def list_products(
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    restaurant_id: Annotated[int | None, Query(gt=0)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    available_only: bool = True,
    q: Annotated[str | None, Query(max_length=80)] = None,
) -> Page[ProductSummary]:
    """Paginated, filtered, projected catalogue.

    `limit` is capped at 100 by the validator so a client cannot ask for the
    whole collection in one request.

    PROJECTION: the second argument to find() lists exactly the fields
    ProductSummary needs. `description` and `attributes` are never read off
    disk for a list request (team_project.pdf p.7 - projections over eager
    loading). Fetching the full documents here would move megabytes to
    render a menu list.
    """
    query: dict[str, Any] = {}
    if restaurant_id is not None:
        query["restaurant_id"] = restaurant_id
    if category:
        query["category"] = category
    if available_only:
        query["is_available"] = True
    if min_price is not None or max_price is not None:
        price_filter: dict[str, Any] = {}
        if min_price is not None:
            price_filter["$gte"] = float(min_price)
        if max_price is not None:
            price_filter["$lte"] = float(max_price)
        query["price"] = price_filter
    if q:
        query["$text"] = {"$search": q}

    projection = {
        "_id": 1,
        "restaurant_id": 1,
        "name": 1,
        "category": 1,
        "price": 1,
        "currency": 1,
        "is_available": 1,
    }

    total = await mongo.products().count_documents(query)
    cursor = (
        mongo.products()
        .find(query, projection)
        .sort("name", 1)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    items = [ProductSummary.model_validate(_serialize(doc)) async for doc in cursor]

    return Page[ProductSummary](
        items=items,
        page=page,
        limit=limit,
        total=total,
        pages=math.ceil(total / limit) if limit else 0,
    )


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: str) -> ProductOut:
    """Full document including attributes - the detail view."""
    try:
        oid = ObjectId(product_id)
    except InvalidId as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed product_id") from exc

    doc = await mongo.products().find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return ProductOut.model_validate(_serialize(doc))


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.STAFF, UserRole.ADMIN))],
)
async def create_product(payload: ProductCreate) -> ProductOut:
    """Create a menu item with arbitrary dynamic attributes.

    `attributes` is stored verbatim. A curry sends spice_level and protein;
    a pizza sends size, crust and toppings[]. Both land in the same
    collection with no schema change, which is the requirement CP1 §3 is
    testing.

    Staff/admin only - an open catalogue-write endpoint is a defacement API.
    """
    now = datetime.now(UTC)
    doc = payload.model_dump()
    doc["price"] = float(payload.price)  # BSON has no Decimal128 in this model
    doc["created_at"] = now
    doc["updated_at"] = now

    result = await mongo.products().insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return ProductOut.model_validate(doc)
