"""Auth router - issues the bearer tokens that make 401 meaningful."""

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
    user = await session.scalar(select(User).where(User.email == payload.email))

    # One generic message for "no such user" and "wrong password". Telling
    # them apart turns this endpoint into an account-existence oracle.
    if user is None or not verify_password(payload.password, user.password_hash):
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
