"""Reservations router - double-booking prevention (409 Conflict)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_session
from app.models.reservation import Reservation
from app.models.user import UserRole
from app.schemas.reservation import ReservationCreate, ReservationOut, ReservationUpdate
from app.security import CurrentUser
from app.services import reservations as svc

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])

Session = Annotated[AsyncSession, Depends(get_session)]


async def _load_owned(
    reservation_id: int, session: AsyncSession, current
) -> Reservation:
    reservation = await session.scalar(
        select(Reservation).where(Reservation.id == reservation_id)
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
    payload: ReservationCreate, session: Session, current: CurrentUser
) -> ReservationOut:
    """Book a table.

    Concurrent requests for the same table and overlapping window: exactly
    one receives 201, every other receives 409. Enforced by the database
    EXCLUDE constraint, not by application-level checking.
    """
    reservation = await svc.create_reservation(session, current, payload)
    return svc.to_out(reservation)


@router.get("/{reservation_id}", response_model=ReservationOut)
async def get_reservation(
    reservation_id: int, session: Session, current: CurrentUser
) -> ReservationOut:
    reservation = await _load_owned(reservation_id, session, current)
    return svc.to_out(reservation)


@router.patch("/{reservation_id}", response_model=ReservationOut)
async def update_reservation(
    reservation_id: int,
    payload: ReservationUpdate,
    session: Session,
    current: CurrentUser,
) -> ReservationOut:
    reservation = await _load_owned(reservation_id, session, current)

    if payload.party_size is not None:
        reservation.party_size = payload.party_size
    if payload.status is not None:
        reservation.status = payload.status

    await session.commit()
    await session.refresh(reservation)
    return svc.to_out(reservation)


@router.delete("/{reservation_id}", response_model=ReservationOut)
async def cancel_reservation(
    reservation_id: int, session: Session, current: CurrentUser
) -> ReservationOut:
    """Cancel a reservation.

    Sets status to 'cancelled' rather than deleting the row. The EXCLUDE
    constraint ignores cancelled rows, so this frees the slot while
    retaining the history.
    """
    reservation = await _load_owned(reservation_id, session, current)
    reservation = await svc.cancel_reservation(session, reservation)
    return svc.to_out(reservation)
