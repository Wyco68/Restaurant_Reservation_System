"""Reservations router - double-booking prevention (409 Conflict) and
optimistic concurrency on edits (If-Match / ETag)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_session
from app.models.reservation import Reservation
from app.models.user import UserRole
from app.schemas.reservation import (
    ReservationCreate,
    ReservationListItem,
    ReservationOut,
    ReservationUpdate,
)
from app.security import CurrentUser
from app.services import reservations as svc

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])

Session = Annotated[AsyncSession, Depends(get_session)]
IfMatch = Annotated[str | None, Header(description="The reservation's ETag")]


async def _load_owned(
    reservation_id: int, session: AsyncSession, current
) -> Reservation:
    reservation = await session.scalar(
        svc.select_reservations().where(Reservation.id == reservation_id)
    )
    if reservation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    if current.role == UserRole.CUSTOMER and reservation.user_id != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your reservation")
    return reservation


@router.post(
    "",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Validation failed or party exceeds table capacity"},
        401: {"description": "Missing or invalid token"},
        404: {"description": "Table not found"},
        409: {"description": "Table already booked for an overlapping window"},
    },
)
async def create_reservation(
    payload: ReservationCreate, session: Session, current: CurrentUser, response: Response
) -> ReservationOut:
    """Book a table.

    Concurrent requests for the same table and overlapping window: exactly
    one receives 201, every other receives 409. Enforced by the database
    EXCLUDE constraint, not by application-level checking.
    """
    reservation = await svc.create_reservation(session, current, payload)
    response.headers["ETag"] = svc.etag(reservation)
    return svc.to_out(reservation)


@router.get(
    "",
    response_model=list[ReservationListItem],
    responses={401: {"description": "Missing or invalid token"}},
)
async def list_my_reservations(
    session: Session,
    current: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ReservationListItem]:
    """The signed-in user's own reservations, latest first, cancelled included.

    Always scoped to the caller - staff read other users' bookings one at a
    time through GET /{id}, never as a bulk list. One SQL query whatever the
    number of bookings: see svc.select_my_bookings.
    """
    rows = await session.execute(svc.select_my_bookings(current.id, limit))
    return [svc.to_list_item(row) for row in rows]


@router.get("/{reservation_id}", response_model=ReservationOut)
async def get_reservation(
    reservation_id: int, session: Session, current: CurrentUser, response: Response
) -> ReservationOut:
    reservation = await _load_owned(reservation_id, session, current)
    response.headers["ETag"] = svc.etag(reservation)
    return svc.to_out(reservation)


@router.patch(
    "/{reservation_id}",
    response_model=ReservationOut,
    responses={
        409: {"description": "A concurrent request changed it first; re-read and retry"},
        412: {"description": "If-Match is not the current version; re-read and retry"},
        428: {"description": "If-Match header missing"},
    },
)
async def update_reservation(
    reservation_id: int,
    payload: ReservationUpdate,
    session: Session,
    current: CurrentUser,
    response: Response,
    if_match: IfMatch = None,
) -> ReservationOut:
    """Partial update, optimistic concurrency.

    If-Match is required: a client may only change the version it has seen.
    A new guest_name is dual-written, same as on create.
    """
    reservation = await _load_owned(reservation_id, session, current)
    svc.check_if_match(reservation, if_match, required=True)
    reservation = await svc.update_reservation(session, reservation, payload)
    response.headers["ETag"] = svc.etag(reservation)
    return svc.to_out(reservation)


@router.delete(
    "/{reservation_id}",
    response_model=ReservationOut,
    responses={
        409: {"description": "A concurrent request changed it first"},
        412: {"description": "If-Match sent and not the current version"},
    },
)
async def cancel_reservation(
    reservation_id: int,
    session: Session,
    current: CurrentUser,
    response: Response,
    if_match: IfMatch = None,
) -> ReservationOut:
    """Cancel a reservation.

    Sets status to 'cancelled' rather than deleting the row. The EXCLUDE
    constraint ignores cancelled rows, so this frees the slot while
    retaining the history. If-Match is optional here: cancelling does not
    depend on the fields the client saw, but it is honoured when sent.
    """
    reservation = await _load_owned(reservation_id, session, current)
    svc.check_if_match(reservation, if_match, required=False)
    reservation = await svc.cancel_reservation(session, reservation)
    response.headers["ETag"] = svc.etag(reservation)
    return svc.to_out(reservation)
