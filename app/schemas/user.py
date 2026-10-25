"""User request and response schemas.

Pydantic checks every request before the handler runs, which gives the
422 responses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    # Signup always creates a customer. Only an admin can change roles.


class UserUpdate(BaseModel):
    """Profile edit. All fields are optional and only the ones sent are
    changed. Role, password and is_active cannot be changed here.
    """

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    # null clears the phone number; leaving it out keeps it.
    phone: str | None = Field(default=None, max_length=32)


class UserOut(BaseModel):
    """User response. password_hash is never included."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    phone: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
