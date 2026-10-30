"""Shared API schemas: pagination primitives."""

from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class Pagination(BaseModel):
    """Common limit/offset query parameters for list endpoints."""

    limit: int = Query(default=20, ge=1, le=100, description="Page size")
    offset: int = Query(default=0, ge=0, description="Number of items to skip")


class Page(BaseModel, Generic[ItemT]):
    """Envelope returned by every list endpoint."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int
