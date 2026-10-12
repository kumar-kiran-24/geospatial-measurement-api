"""Reading KML and Shapefile datasets into a format-agnostic structure.

The reader is the only place that knows about GDAL/pyogrio/GeoPandas. It returns
plain Python objects, which keeps the measurement engine independently testable
and makes swapping the IO layer (for example to Fiona or to an object store)
a local change.

Reading strategy
----------------
* ``pyogrio.read_info`` is called first. It is cheap, and it lets the service
  enforce the feature-count guard rail and capture the CRS *before* any
  geometries are materialised in memory.
* Source attributes are preserved verbatim (coerced to JSON-safe values).
* Geometry problems (missing, empty, invalid) are **not** raised here: they are
  data characteristics that the measurement engine reports per feature.

CRS policy
----------
The CRS reported by the driver is preserved as-is. No CRS is ever assumed or
defaulted. A note on KML: the OGC KML specification mandates WGS84 longitude /
latitude, so reporting ``EPSG:4326`` for a KML file is a property of the format,
not an assumption made by this code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyogrio
from pyproj import CRS
from shapely.geometry import GeometryCollection
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import EmptyDatasetError, GeospatialParseError, TooManyFeaturesError
from app.core.logging import get_logger
from app.models.enums import FileFormat
from app.utils.json_safe import to_json_safe_mapping

logger = get_logger(__name__)

#: Attribute names that are treated as a stable source feature identifier.
_FEATURE_ID_KEYS = ("id", "ID", "fid", "FID", "objectid", "OBJECTID", "uuid", "UUID")


@dataclass(frozen=True)
class RawFeature:
    """A feature exactly as read from the source, before measurement."""

    index: int
    feature_id: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any]


@dataclass(frozen=True)
class ReadDataset:
    """Everything the processing pipeline needs from the source file."""

    features: tuple[RawFeature, ...]
    source_crs: CRS | None
    source_crs_wkt: str | None
    declared_geometry_type: str | None
    driver: str
    bounds: tuple[float, float, float, float] | None


class GeospatialReader:
    """Reads supported geospatial containers into :class:`ReadDataset`."""

    def __init__(self, max_features: int) -> None:
        self.max_features = max_features

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def read(
        self,
        path: Path,
        file_format: FileFormat,
        *,
        layer: str | None = None,
    ) -> ReadDataset:
        path = Path(path)
        head = self._read_info(path, layer)
        feature_count = int(head.get("features") or 0)

        if feature_count > self.max_features:
            raise TooManyFeaturesError(
                details={"feature_count": feature_count, "limit": self.max_features}
            )
        if feature_count == 0:
            raise EmptyDatasetError(
                "The geospatial file does not contain any features.",
                details={"driver": head.get("driver"), "file_format": file_format.value},
            )

        frame = self._read_frame(path, layer)
        source_crs = self._extract_crs(frame)
        raw_crs_wkt = head.get("crs") or (source_crs.to_wkt() if source_crs else None)

        features = tuple(self._to_features(frame))
        geometry_types = sorted({f.geometry.geom_type for f in features if f.geometry is not None})

        logger.info(
            "geospatial_read_completed",
            extra={
                "driver": head.get("driver"),
                "feature_count": len(features),
                "geometry_types": geometry_types,
                "has_crs": source_crs is not None,
            },
        )

        return ReadDataset(
            features=features,
            source_crs=source_crs,
            source_crs_wkt=raw_crs_wkt,
            declared_geometry_type=head.get("geometry_type"),
            driver=str(head.get("driver") or file_format.value),
            bounds=self._bounds(frame, head),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _read_info(self, path: Path, layer: str | None) -> dict[str, Any]:
        try:
            return dict(pyogrio.read_info(str(path), layer=layer))
        except Exception as exc:  # noqa: BLE001 - driver errors are opaque
            logger.warning(
                "geospatial_metadata_read_failed",
                extra={"error": type(exc).__name__, "detail": str(exc)[:500]},
            )
            raise GeospatialParseError(
                "The geospatial file could not be opened or its metadata is unreadable.",
                details={"reason": "metadata_unreadable"},
            ) from exc

    def _read_frame(self, path: Path, layer: str | None) -> gpd.GeoDataFrame:
        try:
            frame = gpd.read_file(str(path), layer=layer, engine="pyogrio")
        except Exception as exc:  # noqa: BLE001 - driver errors are opaque
            logger.warning(
                "geospatial_read_failed",
                extra={"error": type(exc).__name__, "detail": str(exc)[:500]},
            )
            raise GeospatialParseError(
                "The geospatial file could not be parsed. It may be malformed or truncated.",
                details={"reason": "parse_failed"},
            ) from exc

        if not isinstance(frame, gpd.GeoDataFrame):  # pragma: no cover - defensive
            frame = gpd.GeoDataFrame(frame)
        return frame

    @staticmethod
    def _extract_crs(frame: gpd.GeoDataFrame) -> CRS | None:
        """Return the frame CRS, treating unset/invalid CRS as unknown."""
        raw = getattr(frame, "crs", None)
        if raw is None:
            return None
        if isinstance(raw, CRS):
            return raw
        try:
            return CRS.from_user_input(raw)
        except Exception:  # noqa: BLE001 - unparseable CRS is "unknown", not fatal
            logger.warning("source_crs_unparseable")
            return None

    def _to_features(self, frame: gpd.GeoDataFrame) -> list[RawFeature]:
        geometry_name = frame.geometry.name if hasattr(frame, "geometry") else None
        attribute_columns = [c for c in frame.columns if c != geometry_name]
        # A driver that exposes real feature ids replaces the default RangeIndex.
        index_is_meaningful = list(frame.index) != list(range(len(frame)))

        features: list[RawFeature] = []
        for position, (index_value, row) in enumerate(frame.iterrows()):
            geometry = row.get(geometry_name) if geometry_name else None
            shapely_geometry = geometry if isinstance(geometry, BaseGeometry) else None
            if isinstance(shapely_geometry, GeometryCollection) and shapely_geometry.is_empty:
                shapely_geometry = None

            properties = to_json_safe_mapping(
                {column: row.get(column) for column in attribute_columns}
            )
            features.append(
                RawFeature(
                    index=position,
                    feature_id=self._feature_id(index_value, index_is_meaningful, properties),
                    geometry=shapely_geometry,
                    properties=properties,
                )
            )
        return features

    @staticmethod
    def _feature_id(
        index_value: Any, index_is_meaningful: bool, properties: dict[str, Any]
    ) -> str | None:
        for key in _FEATURE_ID_KEYS:
            value = properties.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                text = str(value).strip()
                if text:
                    return text[:255]
        if index_is_meaningful and index_value is not None:
            return str(index_value)[:255]
        return None

    @staticmethod
    def _bounds(
        frame: gpd.GeoDataFrame, head: dict[str, Any]
    ) -> tuple[float, float, float, float] | None:
        raw = head.get("total_bounds")
        if raw is not None:
            try:
                values = tuple(float(value) for value in raw)
                if len(values) == 4 and all(value == value for value in values):  # not NaN
                    return values  # type: ignore[return-value]
            except (TypeError, ValueError):  # pragma: no cover - defensive
                pass
        try:
            bounds = frame.total_bounds
            values = tuple(float(value) for value in bounds)
            if len(values) == 4 and all(value == value for value in values):
                return values  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 - bounds are advisory only
            return None
        return None
