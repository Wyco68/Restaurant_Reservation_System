"""Restaurants router - search, detail, availability."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_session
from app.models.reservation import Reservation, ReservationStatus
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
    """Paginated restaurant search.

    PROJECTION: selects six named columns rather than the whole entity, so
    `address` and the owner relationship are never loaded for a result list
    (team_project.pdf p.7). This is the SQLAlchemy equivalent of EF Core's
    .Select().
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
async def get_restaurant(restaurant_id: int, session: Session) -> Restaurant:
    restaurant = await session.scalar(
        select(Restaurant).where(Restaurant.id == restaurant_id)
    )
    if restaurant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found")
    return restaurant


@router.get("/{restaurant_id}/availability", response_model=list[AvailabilitySlot])
async def check_availability(
    restaurant_id: int,
    starts_at: datetime,
    ends_at: datetime,
    session: Session,
    party_size: Annotated[int, Query(ge=1, le=20)] = 2,
) -> list[AvailabilitySlot]:
    """Which tables are free for a window.

    ADVISORY ONLY. A table shown as free here can be taken before the
    booking request arrives - that is an unavoidable race in any
    check-then-act flow. The authoritative answer is the 201-or-409 from
    POST /reservations, which the EXCLUDE constraint decides.
    """
    if ends_at <= starts_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ends_at must be after starts_at")

    tables = (
        await session.scalars(
            select(RestaurantTable).where(
                RestaurantTable.restaurant_id == restaurant_id,
                RestaurantTable.is_active.is_(True),
                RestaurantTable.capacity >= party_size,
            )
        )
    ).all()

    if not tables:
        return []

    # One query for all conflicts rather than one per table - avoiding the
    # N+1 pattern team_project.pdf p.7 calls out.
    busy = set(
        (
            await session.scalars(
                select(Reservation.restaurant_table_id).where(
                    Reservation.restaurant_table_id.in_([t.id for t in tables]),
                    Reservation.status != ReservationStatus.CANCELLED,
                    # Overlap test, mirroring the tstzrange '[)' semantics of
                    # the EXCLUDE constraint: touching endpoints do not clash.
                    Reservation.starts_at < ends_at,
                    Reservation.ends_at > starts_at,
                )
            )
        ).all()
    )

    return [
        AvailabilitySlot(
            restaurant_table_id=t.id,
            table_number=t.table_number,
            capacity=t.capacity,
            available=t.id not in busy,
        )
        for t in tables
    ]


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
