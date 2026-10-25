"""Login, tokens and roles.

Passwords are stored as bcrypt hashes and are never logged or returned.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.postgres import get_session
from app.models.user import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)


# Password hashing. bcrypt takes about 0.2 s per call. We run it in a
# worker thread, because on the event loop it would block every other
# request during that time.


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # A broken hash in the database means the login fails.
        return False


async def hash_password(plain: str) -> str:
    return await run_in_threadpool(_hash, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    return await run_in_threadpool(_check, plain, hashed)


# JWT


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# FastAPI dependencies

# Columns loaded for the current user. password_hash is left out (only
# /auth/login needs it), and raiseload makes any access to it an error.
PROFILE = load_only(
    User.id, User.email, User.full_name, User.phone, User.role, User.is_active,
    User.created_at, raiseload=True,
)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Find the user for the bearer token.

    Returns 401 if the token is missing, invalid, expired or the user no
    longer exists. The user is read from the database on every request, so a
    disabled account stops working right away.
    """
    if credentials is None:
        raise _UNAUTHORIZED

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _UNAUTHORIZED from exc

    user = await session.scalar(select(User).options(PROFILE).where(User.id == user_id))
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*allowed: UserRole):
    """Dependency for endpoints that need a certain role.

    Returns 403 (not 401) when the user is logged in but has the wrong role.
    """

    async def _guard(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(r.value for r in allowed)}",
            )
        return user

    return _guard
