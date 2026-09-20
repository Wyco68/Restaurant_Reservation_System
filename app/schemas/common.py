"""Shared response envelopes."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard pagination envelope.

    Used by every list endpoint so clients learn one shape, not five.
    """

    items: list[T]
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class ErrorResponse(BaseModel):
    """Standard error body. FastAPI's default {"detail": ...} shape, documented."""

    detail: str
