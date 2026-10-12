"""ORM model for a single extracted/measured feature."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base
from app.models.enums import MeasurementStatus, MeasurementType

#: JSONB on PostgreSQL, plain JSON elsewhere. JSONB gives indexing and
#: normalisation of the flexible source attributes; the variant keeps the
#: model usable against SQLite for fast, isolated unit tests.
JsonColumn = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")

_VALID_STATUSES = tuple(status.value for status in MeasurementStatus)
_VALID_TYPES = tuple(mtype.value for mtype in MeasurementType)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


class FeatureMeasurement(Base):
    """Persisted per-feature geometry metadata and measurement result.

    One row per feature, keyed by ``(file_id, feature_index)``. ``feature_index``
    is the position of the feature in the source dataset and therefore
    *stable*: it is the ordering used for pagination, which prevents rows from
    shifting between pages as new files are uploaded.

    ``properties`` holds the original source attributes, coerced to
    JSON-serialisable values, so no attribute information is lost.

    ``geometry`` holds the GeoJSON representation of the feature (coordinates
    rounded to 7 decimal places, roughly 1 cm) so the measurements endpoint can
    return geometry without re-parsing the source file on every request. Storing
    the geometry is a deliberate trade-off: it duplicates data that also exists
    in the uploaded file, but the file is the authoritative artefact and may be
    deleted, while the measurement record is the queryable result set.
    """

    __tablename__ = "feature_measurements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Identity within the dataset -------------------------------------
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Geometry description --------------------------------------------
    geometry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)
    properties: Mapped[dict[str, Any] | None] = mapped_column(JsonColumn, nullable=True)

    # --- Measurement result ----------------------------------------------
    measurement_status: Mapped[str] = mapped_column(String(24), nullable=False)
    measurement_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MeasurementType.NONE.value
    )
    # Canonical SI values only. km / km² / hectares are derived for presentation.
    area_square_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_meters: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Failure detail ---------------------------------------------------
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    file: Mapped["FileRecord"] = relationship(  # noqa: F821
        back_populates="features", lazy="joined"
    )

    __table_args__ = (
        # Enforces one measurement per feature and underpins stable pagination.
        UniqueConstraint("file_id", "feature_index", name="feature_index_unique"),
        CheckConstraint(
            _in_clause("measurement_status", _VALID_STATUSES), name="status_valid"
        ),
        CheckConstraint(_in_clause("measurement_type", _VALID_TYPES), name="type_valid"),
        CheckConstraint("feature_index >= 0", name="feature_index_non_negative"),
        CheckConstraint(
            "area_square_meters IS NULL OR area_square_meters >= 0",
            name="area_non_negative",
        ),
        CheckConstraint(
            "length_meters IS NULL OR length_meters >= 0", name="length_non_negative"
        ),
        Index("ix_feature_measurements_file_id_feature_index", "file_id", "feature_index"),
        Index("ix_feature_measurements_file_id_status", "file_id", "measurement_status"),
        Index("ix_feature_measurements_file_id_geometry_type", "file_id", "geometry_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<FeatureMeasurement file_id={self.file_id} index={self.feature_index} "
            f"type={self.geometry_type} status={self.measurement_status}>"
        )
