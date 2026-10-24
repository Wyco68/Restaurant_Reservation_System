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

Edits are protected by optimistic concurrency (OCC). Every response carries
the row's version (PostgreSQL's xmin, see the model). A PATCH must send it
back in If-Match:

    no If-Match                       -> 428, the client must say what it saw
    If-Match is not the stored version -> 412, someone changed it since
    changed between our read and write -> 409, a concurrent request won

Either way the client re-reads and retries; nothing is silently overwritten.
A lock cannot do this job: the read happened in the client's browser,
minutes and several HTTP requests before the write.
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


def select_my_bookings(user_id, limit: int) -> Select:
    """PROJECTION for the "my bookings" list: one query, named columns only.

    The restaurant name comes from a JOIN. Without it the client fetches
    GET /restaurants/{id} once per distinct restaurant - an N+1 over HTTP.
    Columns rather than entities: a list is read-only, so nothing needs the
    ORM's identity map or change tracking.
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


def _fields(r: Any) -> dict[str, Any]:
    """Response fields from a Reservation or a projected row - same names.

    THIS IS THE SWITCH-READ POINT. Before read_new the name comes from
    guest_name; from read_new on it comes from first_name / last_name only -
    guest_name is not even loaded. The response shape never changes, so
    clients cannot tell which column served them.
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
    """Compare the client's If-Match with the version just read.

    Accepts the forms RFC 9110 allows: "123", W/"123", a comma-separated
    list, or * (any version).
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
    """Commit an UPDATE guarded by the version check.

    StaleDataError means the UPDATE matched 0 rows: between this request's
    read and its write, another transaction committed a change, so xmin no
    longer equals what was read. Under READ COMMITTED the loser waits on the
    winner's row lock, then re-checks the WHERE against the new row version.
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
    """Apply a partial update. A new guest_name is dual-written like a create.

    The caller has already checked If-Match; the version check in the UPDATE
    catches a writer that slipped in after that.
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
    """Cancel a reservation, freeing the slot.

    The EXCLUDE constraint's WHERE clause excludes cancelled rows, so this
    single status change is what makes the time window bookable again. No
    row is deleted - cancellation history is retained.
    """
    reservation.status = ReservationStatus.CANCELLED
    await _commit_versioned(session)
    return await reload(session, reservation)
