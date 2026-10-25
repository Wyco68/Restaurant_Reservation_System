"""Reservation logic: booking, editing, cancelling and the name migration.

Name migration: the columns written and read depend on the phase in
app/migration.py. Writes set migration.write_columns() and reads load
migration.read_columns(), so the same code works on every schema version.

Double booking is stopped by the no_double_booking EXCLUDE constraint in
the database. When two bookings overlap, one insert succeeds and the other
gets an IntegrityError, which we return as 409.

Edits use optimistic locking. Every response has the row's version (xmin)
and PATCH must send it back in If-Match:

    no If-Match                 -> 428
    If-Match is an old version  -> 412
    row changed during request  -> 409

After 412 or 409 the client should read the reservation again and retry.
See docs/DESIGN.md for why we chose these.
"""

from __future__ import annotations

import logging

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer
from sqlalchemy.orm.exc import StaleDataError

from app import migration
from app.models.reservation import Reservation, ReservationStatus
from app.models.restaurant import Restaurant, RestaurantTable
from app.models.user import User
from app.schemas.reservation import (
    ReservationCreate,
    ReservationListItem,
    ReservationOut,
    ReservationUpdate,
)

log = logging.getLogger(__name__)


def split_guest_name(full: str) -> tuple[str, str]:
    """Split guest_name into first and last name at the first space.

    "Mary Jane Watson" gives ("Mary", "Jane Watson") and "Prince" gives
    ("Prince", ""). The backfill script imports this function too, so both
    split names the same way.
    """
    cleaned = " ".join(full.strip().split())
    if not cleaned:
        return "", ""
    first, _, last = cleaned.partition(" ")
    return first[:60], last[:60]


def select_reservations() -> Select[tuple[Reservation]]:
    """SELECT reservations, loading only the name columns the phase reads.

    All reservation queries start here, so they never ask for a name column
    that the current schema does not have.
    """
    return select(Reservation).options(
        *(undefer(getattr(Reservation, c)) for c in migration.read_columns())
    )


def select_my_bookings(user_id, limit: int) -> Select:
    """Query for the "my bookings" list: one query, only the needed columns.

    The restaurant name is joined in, so the frontend does not have to call
    GET /restaurants/{id} for every booking.
    """
    return (
        select(
            Reservation.id,
            Reservation.user_id,
            Reservation.restaurant_id,
            Restaurant.name.label("restaurant_name"),
            Reservation.restaurant_table_id,
            *(getattr(Reservation, c) for c in migration.read_columns()),
            Reservation.party_size,
            Reservation.starts_at,
            Reservation.ends_at,
            Reservation.status,
            Reservation.created_at,
            Reservation.version,
        )
        .join(Restaurant, Restaurant.id == Reservation.restaurant_id)
        .where(Reservation.user_id == user_id)
        .order_by(Reservation.starts_at.desc())
        .limit(limit)
    )


def write_name(r: Reservation, guest_name: str) -> None:
    """Set the name columns that the current phase writes (dual write)."""
    first, last = split_guest_name(guest_name)
    values = {"guest_name": guest_name, "first_name": first, "last_name": last}
    for column in migration.write_columns():
        setattr(r, column, values[column])


async def reload(session: AsyncSession, r: Reservation) -> Reservation:
    """Read the reservation again after a commit."""
    return await session.scalar(
        select_reservations()
        .where(Reservation.id == r.id)
        .execution_options(populate_existing=True)
    )


def _fields(r: Any) -> dict[str, Any]:
    """Build the response fields from a Reservation or a list row.

    This is where the read switches: before read_new the name comes from
    guest_name, after that from first_name and last_name. The response looks
    the same either way.
    """
    if "guest_name" in migration.read_columns():
        guest_name = r.guest_name
    else:
        guest_name = " ".join(p for p in (r.first_name, r.last_name) if p)

    return {
        "id": r.id,
        "user_id": r.user_id,
        "restaurant_id": r.restaurant_id,
        "restaurant_table_id": r.restaurant_table_id,
        "guest_name": guest_name,
        "party_size": r.party_size,
        "starts_at": r.starts_at,
        "ends_at": r.ends_at,
        "status": r.status,
        "created_at": r.created_at,
        "version": int(r.version),
    }


def to_out(r: Reservation) -> ReservationOut:
    return ReservationOut(**_fields(r))


def to_list_item(row: Any) -> ReservationListItem:
    return ReservationListItem(**_fields(row), restaurant_name=row.restaurant_name)


def etag(r: Reservation) -> str:
    return f'"{r.version}"'


def check_if_match(r: Reservation, if_match: str | None, *, required: bool) -> None:
    """Check the client's If-Match against the current version.

    Accepts "123", W/"123", a comma-separated list, or * for any version.
    """
    if if_match is None:
        if required:
            raise HTTPException(
                status.HTTP_428_PRECONDITION_REQUIRED,
                "Send If-Match with the reservation's version (its ETag)",
            )
        return
    seen = {t.strip().removeprefix("W/").strip('"') for t in if_match.split(",")}
    if "*" not in seen and str(r.version) not in seen:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            f"Reservation changed since you read it; current version is {r.version}",
        )


async def _commit_versioned(session: AsyncSession) -> None:
    """Commit an update that is checked against the version.

    StaleDataError means another request changed the row after we read it,
    so the UPDATE matched no rows. We return 409.
    """
    try:
        await session.commit()
    except StaleDataError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Reservation was changed by a concurrent request; re-read and retry",
        ) from exc


async def create_reservation(
    session: AsyncSession, user: User, payload: ReservationCreate
) -> Reservation:
    """Create a reservation, or raise 409 if the slot is taken.

    The checks below only give clearer 400/404 messages. The EXCLUDE
    constraint is what stops double booking.
    """
    # Lock the table's row until commit so bookings for the same table wait
    # for each other. Without it, two overlapping inserts can deadlock while
    # PostgreSQL checks the constraint, and one of them fails with a 500.
    # NO KEY UPDATE still lets foreign key checks on the row go through.
    table = await session.scalar(
        select(RestaurantTable)
        .where(RestaurantTable.id == payload.restaurant_table_id)
        .with_for_update(key_share=True)
    )
    if table is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Table not found")
    if table.restaurant_id != payload.restaurant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Table does not belong to that restaurant")
    if not table.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Table is not bookable")
    if payload.party_size > table.capacity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Party of {payload.party_size} exceeds table capacity of {table.capacity}",
        )

    reservation = Reservation(
        user_id=user.id,
        restaurant_id=payload.restaurant_id,
        restaurant_table_id=payload.restaurant_table_id,
        party_size=payload.party_size,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        status=ReservationStatus.CONFIRMED,
    )
    write_name(reservation, payload.guest_name)
    session.add(reservation)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "no_double_booking" in str(exc.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That table is already booked for an overlapping time window",
            ) from exc
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reservation violates a constraint") from exc

    return await reload(session, reservation)


async def update_reservation(
    session: AsyncSession, reservation: Reservation, payload: ReservationUpdate
) -> Reservation:
    """Apply a partial update. A new guest_name is written like on create.

    The router has already checked If-Match. The version check in the UPDATE
    catches a change made after that.
    """
    if payload.guest_name is not None:
        write_name(reservation, payload.guest_name)
    if payload.party_size is not None:
        reservation.party_size = payload.party_size
    if payload.status is not None:
        reservation.status = payload.status

    await _commit_versioned(session)
    return await reload(session, reservation)


async def cancel_reservation(
    session: AsyncSession, reservation: Reservation
) -> Reservation:
    """Cancel a reservation and free the slot.

    The EXCLUDE constraint ignores cancelled rows, so changing the status is
    enough. The row is kept for history.
    """
    reservation.status = ReservationStatus.CANCELLED
    await _commit_versioned(session)
    return await reload(session, reservation)
