"""Reservation logic: double-booking prevention and the CP2 read-switch.

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
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.reservation import Reservation, ReservationStatus
from app.models.restaurant import RestaurantTable
from app.models.user import User
from app.schemas.reservation import ReservationCreate, ReservationOut

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


def to_out(r: Reservation) -> ReservationOut:
    """Build the response body.

    THIS IS THE CP2 SWITCH-READ POINT.

    The response shape is identical either way; only the source column
    changes. Rollback is flipping READ_NEW_NAME_FIELDS back to false - a
    config change, not a redeploy, and not a migration.
    """
    if settings.read_new_name_fields:
        guest_name = " ".join(p for p in (r.first_name, r.last_name) if p).strip()
        # Defensive fallback: if the backfill has not yet reached this row,
        # serve the legacy value rather than an empty string. This is what
        # makes the switch safe to flip mid-backfill.
        if not guest_name:
            guest_name = r.guest_name
    else:
        guest_name = r.guest_name

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

    first, last = split_guest_name(payload.guest_name)

    reservation = Reservation(
        user_id=user.id,
        restaurant_id=payload.restaurant_id,
        restaurant_table_id=payload.restaurant_table_id,
        # --- CP2 DUAL-WRITE ---
        # Legacy and new columns are written together on every insert. This
        # is what lets the read switch flip safely in either direction.
        guest_name=payload.guest_name,
        first_name=first,
        last_name=last,
        party_size=payload.party_size,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        status=ReservationStatus.CONFIRMED,
    )
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

    await session.refresh(reservation)
    return reservation


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
    await session.refresh(reservation)
    return reservation
