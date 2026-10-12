"""Enumerations shared by models, schemas and services.

They are stored as ``VARCHAR`` columns with ``CHECK`` constraints rather than
native PostgreSQL ``ENUM`` types: adding a value to a native enum requires a
dedicated migration and cannot be done inside a transaction on older servers,
whereas widening a check constraint is trivial. The trade-off (slightly larger
storage, no implicit ordering) is acceptable at this scale.
"""

from __future__ import annotations

from enum import StrEnum


class FileFormat(StrEnum):
    """Detected container format of the uploaded file (never trusted from the
    extension alone)."""

    KML = "kml"
    SHAPEFILE = "shapefile"


class ProcessingStatus(StrEnum):
    """Lifecycle of an uploaded file.

    ``PENDING``  -> row created, stored on disk, processing not started.
    ``PROCESSING`` -> reading/measuring in progress.
    ``COMPLETED`` -> features extracted and measurements persisted.
    ``FAILED``   -> file-level failure; ``error_code``/``error_message`` set.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(StrEnum):
    """Per-feature measurement outcome.

    ``MEASURED``       - a value was produced.
    ``NOT_APPLICABLE`` - geometry carries no measure (points: coordinates only).
    ``NOT_MEASURABLE`` - the feature was understood but cannot be measured
                         (missing CRS, empty geometry, zero-length ring).
    ``FAILED``         - measuring raised an unexpected error for this feature.
    """

    MEASURED = "MEASURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_MEASURABLE = "NOT_MEASURABLE"
    FAILED = "FAILED"


class MeasurementType(StrEnum):
    """Which measure a feature carries."""

    AREA = "area"
    LENGTH = "length"
    NONE = "none"


class MeasurementStrategy(StrEnum):
    """How planar/geodesic measurement was performed for a whole dataset.

    ``PROJECTED_SOURCE`` - the file already carried a projected CRS with linear
        units; measurement is planar in that CRS (converted to metres).
    ``LOCAL_EQUAL_AREA`` - geographic source covering a local extent; area is
        measured in a Lambert Azimuthal Equal-Area projection centred on the
        dataset, which preserves area.
    ``LOCAL_UTM``       - geographic source covering a local extent; length is
        measured planar in the matching UTM zone (conformal, low scale error).
    ``GEODESIC``        - the extent spans more than one projection zone (or is
        polar/global); measurement is done on the ellipsoid with pyproj.Geod.
    ``NONE``            - no measurement was possible (e.g. unknown CRS).
    """

    PROJECTED_SOURCE = "PROJECTED_SOURCE"
    LOCAL_EQUAL_AREA = "LOCAL_EQUAL_AREA"
    LOCAL_UTM = "LOCAL_UTM"
    GEODESIC = "GEODESIC"
    NONE = "NONE"


class CrsKind(StrEnum):
    """Classification of the source CRS, used to pick a measurement strategy."""

    GEOGRAPHIC = "geographic"
    PROJECTED = "projected"
    UNKNOWN = "unknown"
