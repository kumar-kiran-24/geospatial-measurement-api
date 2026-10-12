"""Shared response schemas: errors, pagination and health."""

from __future__ import annotations

import math
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class ErrorDetail(BaseModel):
    """Machine-readable error payload returned for every failure."""

    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable, client-safe description.")
    details: dict[str, Any] | None = Field(
        default=None, description="Optional structured context (for example, per-field issues)."
    )
    request_id: str | None = Field(
        default=None, description="Correlation id; also present in the server logs."
    )


class ErrorResponse(BaseModel):
    """Envelope used by all error responses."""

    error: ErrorDetail


class PageParams(BaseModel):
    """Validated pagination query parameters."""

    page: int = Field(default=1, ge=1, description="1-based page number.")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Items per page (1-{MAX_PAGE_SIZE}).",
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    @classmethod
    def as_query(
        cls,
        page: int = Query(1, ge=1, description="1-based page number."),
        page_size: int = Query(
            DEFAULT_PAGE_SIZE,
            ge=1,
            le=MAX_PAGE_SIZE,
            description=f"Items per page (1-{MAX_PAGE_SIZE}).",
        ),
    ) -> "PageParams":
        """FastAPI dependency factory."""
        return cls(page=page, page_size=page_size)


class PaginationMeta(BaseModel):
    """Pagination metadata returned alongside any paginated collection."""

    page: int = Field(description="Current page (1-based).")
    page_size: int = Field(description="Maximum items per page.")
    total_items: int = Field(description="Total number of items across all pages.")
    total_pages: int = Field(description="Total number of pages.")
    has_next: bool = Field(description="Whether a further page exists.")
    has_previous: bool = Field(description="Whether a previous page exists.")

    @classmethod
    def build(cls, *, page: int, page_size: int, total_items: int) -> "PaginationMeta":
        total_pages = math.ceil(total_items / page_size) if total_items else 0
        return cls(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1 and total_pages > 0,
        )


class Page(BaseModel, Generic[T]):
    """Generic paginated envelope."""

    items: list[T]
    pagination: PaginationMeta


class HealthResponse(BaseModel):
    """Liveness response (no dependencies are touched)."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(default="ok", description="Always 'ok' when the process is alive.")
    app_name: str
    version: str
    environment: str


class DependencyStatus(BaseModel):
    """Result of a single readiness dependency probe."""

    name: str
    healthy: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Readiness response including dependency state."""

    status: str = Field(description="'ready' when every dependency is healthy, else 'degraded'.")
    checks: list[DependencyStatus]
