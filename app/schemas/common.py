from typing import Generic, TypeVar

from pydantic import BaseModel, Field


class Pagination(BaseModel):
    next_cursor: str | None = None
    limit: int
    has_more: bool


class Meta(BaseModel):
    pagination: Pagination | None = None


T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Every endpoint returns this shape — `data` is a single object for
    singleton resources or a list for collections, and `meta.pagination`
    is populated only for paginated list endpoints — so clients parse
    every response the same way."""

    data: T
    meta: Meta = Field(default_factory=Meta)
