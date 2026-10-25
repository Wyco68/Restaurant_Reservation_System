"""Response shapes shared by several endpoints."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Pagination format used by every paged list endpoint."""

    items: list[T]
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class ErrorResponse(BaseModel):
    """Error body, the same {"detail": ...} format FastAPI uses."""

    detail: str
