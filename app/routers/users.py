"""Users.

POST  /api/v1/users        -> 201
GET   /api/v1/users/{id}   -> 200
PATCH /api/v1/users/me     -> 200
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
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.security import PROFILE, CurrentUser, hash_password

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
    """Register an account. This is the only endpoint that needs no login."""
    user = User(
        email=payload.email,
        password_hash=await hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        role=UserRole.CUSTOMER,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # The unique index on email raises this. Checking first and then
        # inserting would fail when two signups arrive together.
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
    """Get a user's account details.

    Customers can only read their own account. Staff and admin can read
    any account.
    """
    if current.role == UserRole.CUSTOMER and current.id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot read another user's account")

    user = await session.scalar(select(User).options(PROFILE).where(User.id == user_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/me/profile", response_model=UserOut)
async def get_own_profile(current: CurrentUser) -> User:
    """The caller's own profile, so the frontend does not need its own id."""
    return current


@router.patch(
    "/me",
    response_model=UserOut,
    responses={
        401: {"description": "Missing or invalid token"},
        409: {"description": "Email already registered"},
        422: {"description": "Validation failed"},
    },
)
async def update_own_profile(payload: UserUpdate, session: Session, current: CurrentUser) -> User:
    """Edit the caller's name, email or phone.

    Only the fields in the request are changed. full_name and email cannot
    be empty, so a null for them is ignored.
    """
    changes = payload.model_dump(exclude_unset=True)
    for field in ("email", "full_name"):
        if changes.get(field) is None:
            changes.pop(field, None)
    for field, value in changes.items():
        setattr(current, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Same unique index on email as for signup.
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc

    await session.refresh(current)
    return current
