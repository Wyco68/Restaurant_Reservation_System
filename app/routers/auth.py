"""Login. Returns the bearer token used by the other endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.postgres import get_session
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: Session) -> TokenResponse:
    # Only the columns needed to check the login.
    user = (
        await session.execute(
            select(User.id, User.password_hash, User.role, User.is_active).where(
                User.email == payload.email
            )
        )
    ).first()
    # End the transaction before bcrypt (about 0.2 s), so the database
    # connection goes back to the pool while we wait.
    await session.rollback()

    # Same message for "no such user" and "wrong password", so the endpoint
    # does not tell anyone which emails are registered.
    if user is None or not await verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is disabled")

    return TokenResponse(
        access_token=create_access_token(user.id, user.role.value),
        expires_in=settings.jwt_expire_minutes * 60,
    )
