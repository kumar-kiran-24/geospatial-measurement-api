"""ORM model for an uploaded geospatial file."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    CrsKind,
    FileFormat,
    MeasurementStrategy,
    ProcessingStatus,
)

_VALID_STATUSES = tuple(status.value for status in ProcessingStatus)
_VALID_FORMATS = tuple(fmt.value for fmt in FileFormat)
_VALID_CRS_KINDS = tuple(kind.value for kind in CrsKind)
_VALID_STRATEGIES = tuple(strategy.value for strategy in MeasurementStrategy)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


class FileRecord(Base):
    """A stored geospatial file and the state of its processing.

    Notes on the schema
    -------------------
    * The primary key is a UUID generated in the application. It is the only
      identifier exposed to clients, so it never leaks insertion order or row
      counts.
    * ``storage_path`` is a *relative* path below the configured storage root.
      Storing it relative means the storage root can be relocated (for example
      when mounted at a different path inside a container) without rewriting
      rows.
    * ``feature_count`` is stored even though it can be derived from
      ``feature_measurements``: the count is meaningful for ``FAILED`` and
      ``PENDING`` rows, which by definition have no measurement rows. Derived
      aggregates that *only* exist for completed files (geometry type counts)
      are computed with SQL aggregates instead of being duplicated here.
    * Measurement values are stored once, in canonical SI units. Square
      kilometres, hectares and kilometres are presentation concerns and are
      derived in the API schema layer.
    """

    __tablename__ = "uploaded_files"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- Provenance -------------------------------------------------------
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_format: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- Processing state -------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ProcessingStatus.PENDING.value
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Geospatial metadata ---------------------------------------------
    feature_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_crs_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_crs_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    measurement_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    measurement_crs: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Timestamps -------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    features: Mapped[list["FeatureMeasurement"]] = relationship(  # noqa: F821
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="FeatureMeasurement.feature_index",
    )

    __table_args__ = (
        CheckConstraint(_in_clause("status", _VALID_STATUSES), name="status_valid"),
        CheckConstraint(_in_clause("file_format", _VALID_FORMATS), name="format_valid"),
        CheckConstraint(
            f"source_crs_kind IS NULL OR {_in_clause('source_crs_kind', _VALID_CRS_KINDS)}",
            name="crs_kind_valid",
        ),
        CheckConstraint(
            "measurement_strategy IS NULL OR "
            f"{_in_clause('measurement_strategy', _VALID_STRATEGIES)}",
            name="strategy_valid",
        ),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint(
            "feature_count IS NULL OR feature_count >= 0", name="feature_count_valid"
        ),
        Index("ix_uploaded_files_created_at", "created_at"),
        Index("ix_uploaded_files_status", "status"),
        Index("ix_uploaded_files_checksum_sha256", "checksum_sha256"),
    )

    @property
    def processing_error(self):
        if self.error_code:
            # Return a simple object with code/message attributes for Pydantic
            class _Error:
                pass
            err = _Error()
            err.code = self.error_code
            err.message = self.error_message or ""
            return err
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<FileRecord id={self.id} filename={self.original_filename!r} "
            f"status={self.status}>"
        )
