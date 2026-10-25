"""Restaurants: search, detail and table availability."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_session
from app.models.reservation import Reservation
from app.models.restaurant import Restaurant, RestaurantTable
from app.models.user import UserRole
from app.schemas.common import Page
from app.schemas.restaurant import (
    AvailabilitySlot,
    RestaurantCreate,
    RestaurantOut,
    RestaurantSummary,
)
from app.security import CurrentUser, require_role

router = APIRouter(prefix="/api/v1/restaurants", tags=["restaurants"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=Page[RestaurantSummary])
async def search_restaurants(
    session: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    city: Annotated[str | None, Query(max_length=80)] = None,
    cuisine: Annotated[str | None, Query(max_length=60)] = None,
    max_price_range: Annotated[int | None, Query(ge=1, le=4)] = None,
    min_rating: Annotated[float | None, Query(ge=1, le=5)] = None,
) -> Page[RestaurantSummary]:
    """Search restaurants, with paging.

    Only the six columns the list shows are selected, so address and owner
    are not loaded.
    """
    filters = []
    if city:
        filters.append(Restaurant.city == city)
    if cuisine:
        filters.append(Restaurant.cuisine == cuisine)
    if max_price_range is not None:
        filters.append(Restaurant.price_range <= max_price_range)
    if min_rating is not None:
        filters.append(Restaurant.avg_rating >= min_rating)

    where = and_(*filters) if filters else True

    total = await session.scalar(
        select(func.count()).select_from(Restaurant).where(where)
    )

    rows = await session.execute(
        select(
            Restaurant.id,
            Restaurant.name,
            Restaurant.cuisine,
            Restaurant.city,
            Restaurant.price_range,
            Restaurant.avg_rating,
        )
        .where(where)
        .order_by(Restaurant.avg_rating.desc().nullslast(), Restaurant.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )

    items = [RestaurantSummary.model_validate(r, from_attributes=True) for r in rows]
    return Page[RestaurantSummary](
        items=items,
        page=page,
        limit=limit,
        total=total or 0,
        pages=math.ceil((total or 0) / limit) if limit else 0,
    )


@router.get("/{restaurant_id}", response_model=RestaurantOut)
async def get_restaurant(restaurant_id: int, session: Session) -> RestaurantOut:
    """Selects only the columns RestaurantOut needs (no owner_id)."""
    row = (
        await session.execute(
            select(
                Restaurant.id,
                Restaurant.name,
                Restaurant.cuisine,
                Restaurant.city,
                Restaurant.price_range,
                Restaurant.avg_rating,
                Restaurant.address,
                Restaurant.created_at,
            ).where(Restaurant.id == restaurant_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found")
    return RestaurantOut.model_validate(row, from_attributes=True)


@router.get("/{restaurant_id}/availability", response_model=list[AvailabilitySlot])
async def check_availability(
    restaurant_id: int,
    starts_at: datetime,
    ends_at: datetime,
    session: Session,
    party_size: Annotated[int, Query(ge=1, le=20)] = 2,
) -> list[AvailabilitySlot]:
    """Which tables are free for a time window.

    This is only a hint. Someone can book a table between this call and
    the booking request. POST /reservations gives the real answer (201 or
    409).
    """
    if ends_at <= starts_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ends_at must be after starts_at")

    # One query for all tables, with a NOT EXISTS check for each one.
    #
    # The overlap check uses the same tstzrange expression and WHERE as the
    # no_double_booking constraint, so PostgreSQL can use its GiST index.
    # 'cancelled' is written into the SQL as a constant, because with a
    # parameter PostgreSQL sometimes did not use the index.
    # docs/PERFORMANCE.md shows both query plans.
    bounds = literal_column("'[)'")
    taken = exists().where(
        Reservation.restaurant_table_id == RestaurantTable.id,
        Reservation.status.op("<>")(literal_column("'cancelled'")),
        func.tstzrange(Reservation.starts_at, Reservation.ends_at, bounds).op("&&")(
            func.tstzrange(starts_at, ends_at, bounds)
        ),
    )
    rows = await session.execute(
        select(
            RestaurantTable.id.label("restaurant_table_id"),
            RestaurantTable.table_number,
            RestaurantTable.capacity,
            (~taken).label("available"),
        )
        .where(
            RestaurantTable.restaurant_id == restaurant_id,
            RestaurantTable.is_active.is_(True),
            RestaurantTable.capacity >= party_size,
        )
        .order_by(RestaurantTable.id)
    )
    return [AvailabilitySlot.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "",
    response_model=RestaurantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.STAFF, UserRole.ADMIN))],
)
async def create_restaurant(
    payload: RestaurantCreate, session: Session, current: CurrentUser
) -> Restaurant:
    restaurant = Restaurant(**payload.model_dump(), owner_id=current.id)
    session.add(restaurant)
    await session.commit()
    await session.refresh(restaurant)
    return restaurant
