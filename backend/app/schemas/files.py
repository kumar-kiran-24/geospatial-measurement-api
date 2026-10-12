"""Schemas describing uploaded files."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CrsKind,
    FileFormat,
    MeasurementStrategy,
    ProcessingStatus,
)
from app.schemas.common import PaginationMeta


class ProcessingErrorOut(BaseModel):
    """File-level processing failure (safe for clients)."""

    code: str
    message: str


class FileDetail(BaseModel):
    """Full description of an uploaded file.

    Used by ``POST /api/files/``, ``GET /api/files/{id}/`` and as an embedded
    object in the measurements response.
    """

    model_config = ConfigDict(from_attributes=True)

    file_id: uuid.UUID = Field(alias="id", description="Server-generated file identifier.")
    original_filename: str = Field(description="Filename supplied by the client (sanitised for display only).")
    file_format: FileFormat = Field(description="Detected container format.")
    content_type: str | None = Field(default=None, description="Upload Content-Type header, if any.")
    size_bytes: int = Field(description="Size of the stored upload in bytes.")
    checksum_sha256: str = Field(description="SHA-256 of the stored bytes.")
    status: ProcessingStatus = Field(description="Processing lifecycle status.")

    feature_count: int | None = Field(
        default=None, description="Number of features found, once processed."
    )
    source_crs: str | None = Field(
        default=None,
        description="Authoritative source CRS code (for example EPSG:4326), or null when unknown.",
    )
    source_crs_name: str | None = Field(default=None, description="Human-readable source CRS name.")
    source_crs_kind: CrsKind | None = Field(
        default=None, description="Whether the source CRS is geographic, projected or unknown."
    )
    measurement_strategy: MeasurementStrategy | None = Field(
        default=None, description="Strategy used to measure this dataset."
    )
    measurement_crs: str | None = Field(
        default=None,
        description="CRS used for planar measurement, when a projected CRS was used.",
    )

    uploaded_at: datetime = Field(alias="created_at", description="When the upload was accepted (UTC).")
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None

    processing_error: ProcessingErrorOut | None = Field(
        default=None,
        description="Set when status is FAILED; null otherwise.",
    )


class FileListItem(BaseModel):
    """Condensed file record used by the paginated history endpoint."""

    model_config = ConfigDict(from_attributes=True)

    file_id: uuid.UUID = Field(alias="id")
    original_filename: str
    file_format: FileFormat
    status: ProcessingStatus
    feature_count: int | None = None
    source_crs: str | None = None
    size_bytes: int
    uploaded_at: datetime = Field(alias="created_at")
    processing_error: ProcessingErrorOut | None = None


class FileUploadResponse(FileDetail):
    """Response returned after a successful upload + synchronous processing."""


class FileListResponse(BaseModel):
    """Paginated upload history."""

    items: list[FileListItem]
    pagination: PaginationMeta
