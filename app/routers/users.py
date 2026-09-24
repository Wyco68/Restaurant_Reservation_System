"""Users router.

    POST /api/v1/users        -> 201, PostgreSQL
    GET  /api/v1/users/{id}   -> 200, PostgreSQL
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_session
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserOut
from app.security import CurrentUser, hash_password

router = APIRouter(prefix="/api/v1/users", tags=["users"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Validation failed"},
        409: {"description": "Email already registered"},
    },
)
async def create_user(payload: UserCreate, session: Session) -> User:
    """Register an account. Public - this is the one endpoint before auth."""
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        role=UserRole.CUSTOMER,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # The UNIQUE index raises this. Checking for the email first and
        # then inserting would race: two concurrent signups could both pass
        # the check. Let the database be the arbiter.
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc

    await session.refresh(user)
    return user


@router.get(
    "/{user_id}",
    response_model=UserOut,
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Cannot read another user"},
        404: {"description": "User not found"},
    },
)
async def get_user(user_id: uuid.UUID, session: Session, current: CurrentUser) -> User:
    """Fetch account details.

    Authenticated. A customer may read only their own record; staff and
    admin may read any. Without this check the endpoint is a user-data
    enumeration API.
    """
    if current.role == UserRole.CUSTOMER and current.id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot read another user's account")

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/me/profile", response_model=UserOut)
async def get_own_profile(current: CurrentUser) -> User:
    """Convenience endpoint so the frontend need not know its own UUID."""
    return current
