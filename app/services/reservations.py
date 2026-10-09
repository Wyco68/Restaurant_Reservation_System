"""Reservation logic: double-booking prevention and the name migration.

NAME MIGRATION
--------------
Which name columns are written and read depends on the phase in
app/migration.py. Every write sets exactly `migration.write_columns()` and
every read loads exactly `migration.read_columns()`, so the same code serves
the schema before Expand, between the steps, and after Contract.

CONCURRENCY DESIGN
------------------
Double-booking is prevented by a PostgreSQL EXCLUDE constraint, not by
application code:

    EXCLUDE USING gist (
        restaurant_table_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&
    ) WHERE (status <> 'cancelled')

Under concurrent inserts for the same table and overlapping window, exactly
one transaction commits and the rest raise IntegrityError, which this module
translates to 409 Conflict.

Why this and not SELECT ... FOR UPDATE: with a row lock, correctness lives
in application code, and any future code path that forgets to take the lock
silently reintroduces the bug. With the constraint, overlap is structurally
impossible regardless of how many code paths write reservations. See
docs/DESIGN.md for the full comparison.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app import migration
from app.models.reservation import Reservation, ReservationStatus
from app.models.restaurant import RestaurantTable
from app.models.user import User
from app.schemas.reservation import ReservationCreate, ReservationOut, ReservationUpdate

log = logging.getLogger(__name__)


def split_guest_name(full: str) -> tuple[str, str]:
    """Split a legacy guest_name into first / last.

    Splits on the FIRST space only: "Mary Jane Watson" -> ("Mary", "Jane Watson").
    A single-token name gets an empty last name rather than being dropped.
    Used by both the dual-write path and scripts/backfill_names.py, so the
    two can never disagree.
    """
    cleaned = " ".join(full.strip().split())
    if not cleaned:
        return "", ""
    first, _, last = cleaned.partition(" ")
    return first[:60], last[:60]


def select_reservations() -> Select[tuple[Reservation]]:
    """SELECT for whole reservations, loading only the phase's name columns.

    The name columns are deferred on the model, so a plain select(Reservation)
    never names a column that may not exist yet (before Expand) or any more
    (after Contract). Every reservation query starts here.
    """
    return select(Reservation).options(
        *(undefer(getattr(Reservation, c)) for c in migration.read_columns())
    )


def write_name(r: Reservation, guest_name: str) -> None:
    """THE DUAL-WRITE POINT: set exactly the phase's name columns."""
    first, last = split_guest_name(guest_name)
    values = {"guest_name": guest_name, "first_name": first, "last_name": last}
    for column in migration.write_columns():
        setattr(r, column, values[column])


async def reload(session: AsyncSession, r: Reservation) -> Reservation:
    """Re-read after a commit: server defaults plus the phase's name columns."""
    return await session.scalar(
        select_reservations()
        .where(Reservation.id == r.id)
        .execution_options(populate_existing=True)
    )


def to_out(r: Reservation) -> ReservationOut:
    """Build the response body.

    THIS IS THE SWITCH-READ POINT. Before read_new the name comes from
    guest_name; from read_new on it comes from first_name / last_name only -
    guest_name is not even loaded. The response shape never changes, so
    clients cannot tell which column served them.
    """
    if "guest_name" in migration.read_columns():
        guest_name = r.guest_name
    else:
        guest_name = " ".join(p for p in (r.first_name, r.last_name) if p)

    return ReservationOut(
        id=r.id,
        user_id=r.user_id,
        restaurant_id=r.restaurant_id,
        restaurant_table_id=r.restaurant_table_id,
        guest_name=guest_name,
        party_size=r.party_size,
        starts_at=r.starts_at,
        ends_at=r.ends_at,
        status=r.status,
        created_at=r.created_at,
    )


async def create_reservation(
    session: AsyncSession, user: User, payload: ReservationCreate
) -> Reservation:
    """Create a reservation, or raise 409 if the slot is taken.

    Pre-flight checks below produce friendly 400/404 responses for the
    common cases. They are NOT the double-booking defence - the constraint
    is. Removing them would degrade the error messages, not the correctness.
    """
    table = await session.scalar(
        select(RestaurantTable).where(RestaurantTable.id == payload.restaurant_table_id)
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
    """Apply a partial update. A new guest_name is dual-written like a create."""
    if payload.guest_name is not None:
        write_name(reservation, payload.guest_name)
    if payload.party_size is not None:
        reservation.party_size = payload.party_size
    if payload.status is not None:
        reservation.status = payload.status

    await session.commit()
    return await reload(session, reservation)


async def cancel_reservation(
    session: AsyncSession, reservation: Reservation
) -> Reservation:
    """Cancel a reservation, freeing the slot.

    The EXCLUDE constraint's WHERE clause excludes cancelled rows, so this
    single status change is what makes the time window bookable again. No
    row is deleted - cancellation history is retained.
    """
    reservation.status = ReservationStatus.CANCELLED
    await session.commit()
    return await reload(session, reservation)
