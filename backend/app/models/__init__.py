"""ORM models.

Importing this package registers every table on :class:`app.db.base.Base`,
which is what Alembic autogenerate relies on.
"""

from app.models.enums import (
    CrsKind,
    FileFormat,
    MeasurementStatus,
    MeasurementStrategy,
    MeasurementType,
    ProcessingStatus,
)
from app.models.feature_measurement import FeatureMeasurement
from app.models.file_record import FileRecord

__all__ = [
    "CrsKind",
    "FeatureMeasurement",
    "FileFormat",
    "FileRecord",
    "MeasurementStatus",
    "MeasurementStrategy",
    "MeasurementType",
    "ProcessingStatus",
]
