"""Schemas describing measurements and the measurements endpoint payload.

Units are always explicit in the payload. Values are stored once, in canonical
SI units (square metres for area, metres for length); human-friendly units are
derived here so the database never holds duplicated magnitudes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MeasurementStatus, MeasurementType
from app.schemas.common import PaginationMeta
from app.schemas.files import FileDetail

# Presentation rounding. Canonical SI values keep full precision in the
# database; only the serialised representation is rounded.
_AREA_PRECISION = 6
_LARGE_AREA_PRECISION = 9
_LENGTH_PRECISION = 6
_LARGE_LENGTH_PRECISION = 9

SQUARE_METERS_PER_SQUARE_KILOMETER = 1_000_000.0
SQUARE_METERS_PER_HECTARE = 10_000.0
METERS_PER_KILOMETER = 1_000.0


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


class AreaMeasurement(BaseModel):
    """An area expressed in several units, all derived from the same value."""

    square_meters: float = Field(description="Area in square metres (canonical unit).")
    square_kilometers: float = Field(description="Area in square kilometres.")
    hectares: float = Field(description="Area in hectares.")

    @classmethod
    def from_square_meters(cls, square_meters: float) -> "AreaMeasurement":
        return cls(
            square_meters=_round(square_meters, _AREA_PRECISION),
            square_kilometers=_round(
                square_meters / SQUARE_METERS_PER_SQUARE_KILOMETER, _LARGE_AREA_PRECISION
            ),
            hectares=_round(square_meters / SQUARE_METERS_PER_HECTARE, _LARGE_AREA_PRECISION),
        )


class LengthMeasurement(BaseModel):
    """A length expressed in metres and kilometres."""

    meters: float = Field(description="Length in metres (canonical unit).")
    kilometers: float = Field(description="Length in kilometres.")

    @classmethod
    def from_meters(cls, meters: float) -> "LengthMeasurement":
        return cls(
            meters=_round(meters, _LENGTH_PRECISION),
            kilometers=_round(meters / METERS_PER_KILOMETER, _LARGE_LENGTH_PRECISION),
        )


class FeatureError(BaseModel):
    """Why a single feature could not be measured."""

    code: str = Field(description="Stable error code, for example MISSING_CRS.")
    message: str = Field(description="Client-safe explanation.")


class FeatureMeasurementOut(BaseModel):
    """One measured (or deliberately unmeasured) feature."""

    model_config = ConfigDict(from_attributes=True)

    feature_index: int = Field(description="Stable 0-based position in the source dataset.")
    feature_id: str | None = Field(
        default=None, description="Source feature identifier when the format provides one."
    )
    geometry_type: str = Field(description="Geometry type as reported by the reader.")
    measurement_status: MeasurementStatus = Field(description="Outcome of measuring this feature.")
    measurement_type: MeasurementType = Field(description="Which measure applies, if any.")
    area: AreaMeasurement | None = Field(default=None, description="Present when the feature is a polygon.")
    length: LengthMeasurement | None = Field(default=None, description="Present when the feature is a line.")
    error: FeatureError | None = Field(default=None, description="Present when the feature could not be measured.")
    properties: dict[str, Any] | None = Field(default=None, description="Original source attributes.")
    geometry: dict[str, Any] | None = Field(default=None, description="GeoJSON representation of the geometry.")


class MeasurementSummary(BaseModel):
    """Aggregate statistics for the measurements of one file."""

    total_features: int = Field(description="Number of feature records for this file.")
    geometry_type_counts: dict[str, int] = Field(
        default_factory=dict, description="Feature count keyed by geometry type."
    )
    measured_features: int = Field(description="Features with a measurement value.")
    not_applicable_features: int = Field(description="Features that need no measure (points).")
    not_measurable_features: int = Field(description="Features understood but not measurable.")
    failed_features: int = Field(description="Features whose measurement raised an error.")
    area_features: int = Field(description="Number of area measurements.")
    length_features: int = Field(description="Number of length measurements.")
    total_area_square_meters: float = Field(description="Sum of all measured areas.")
    total_length_meters: float = Field(description="Sum of all measured lengths.")


class FileMeasurementsResponse(BaseModel):
    """Response of ``GET /api/files/{file_id}/measurements/``."""

    file: FileDetail = Field(description="Metadata of the measured file.")
    summary: MeasurementSummary = Field(description="Aggregate statistics for the whole file.")
    features: list[FeatureMeasurementOut] = Field(description="The requested page of features.")
    pagination: PaginationMeta = Field(description="Pagination metadata for the features list.")
