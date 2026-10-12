"""Feature extraction, validation, measurement and persistence engine.

The engine is deliberately framework-free so it can be unit tested without
bootstrapping the web layer.  It owns the whole upload lifecycle:

1. reads the source dataset through :class:`GeospatialReader`;
2. validates and, where required, projects every geometry;
3. measures area / length (planar in a locally chosen projected CRS or
   ellipsoidal with ``pyproj.Geod``);
4. writes one :class:`FeatureMeasurement` row per input feature inside a
   single transaction.

Rule upheld here: geographic CRS coordinates are never measured as planar
metres.  A dataset without a CRS is preserved in full but deliberately
unmeasured.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from pyproj import CRS, Geod, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import transform as shapely_transform

from app.core.exceptions import (
    EmptyDatasetError,
    GeospatialParseError,
    TooManyFeaturesError,
)
from app.core.logging import get_logger
from app.models.enums import (
    CrsKind,
    FileFormat,
    MeasurementStatus,
    MeasurementStrategy,
    MeasurementType,
)
from app.models.feature_measurement import FeatureMeasurement
from app.services.crs_service import CrsService, MeasurementPlan
from app.services.file_validation_service import FileValidationService
from app.services.geospatial_reader import ReadDataset, GeospatialReader
from app.utils.geojson import geometry_to_geojson, normalise_geometry_type
from app.utils.json_safe import to_json_safe_mapping

logger = get_logger(__name__)


class ProcessingEngine:
    """Orchestrates reading, measuring and persisting one upload."""

    def __init__(
        self,
        crs_service: CrsService,
        settings: Any,
        validator: FileValidationService,
        storage: Any,
    ) -> None:
        self.crs_service = crs_service
        self.settings = settings
        self.validator = validator
        self.storage = storage
        self._geod_cache: dict[str, Geod] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def process(self, dataset: ReadDataset, file_id: str | None) -> list[FeatureMeasurement]:
        """Read, measure and return measurements for ``dataset``."""
        self._guardrails(dataset)
        measurements = self._measure_all(dataset)
        return measurements

    # ------------------------------------------------------------------ #
    # Reading / validation
    # ------------------------------------------------------------------ #
    def _guardrails(self, dataset: ReadDataset) -> None:
        """Enforce the configured safety limits on the source data."""
        if len(dataset.features) > self.settings.max_features_per_file:
            raise TooManyFeaturesError(
                details={
                    "feature_count": len(dataset.features),
                    "limit": self.settings.max_features_per_file,
                }
            )

    # ------------------------------------------------------------------ #
    # Measurement
    # ------------------------------------------------------------------ #
    def _measure_all(self, dataset: ReadDataset) -> list[FeatureMeasurement]:
        plan = self.crs_service.build_plan(dataset.source_crs, dataset.bounds)

        if not plan.is_measurable:
            return self._unmeasured(dataset, plan.reason or "MISSING_CRS")

        if plan.uses_planar:
            return self._measure_planar(dataset, plan)
        return self._measure_geodesic(dataset, plan)

    def _measure_planar(self, dataset: ReadDataset, plan: MeasurementPlan) -> list[FeatureMeasurement]:
        """Planar measurement after reprojecting the geometry to ``plan.planar_crs``."""
        transformer = Transformer.from_crs(
            dataset.source_crs, plan.planar_crs, always_xy=True
        )

        measurement_type = self._infer_type(dataset)
        measurements: list[FeatureMeasurement] = []

        for feature in dataset.features:
            geometry = self._reproject_geometry(feature.geometry, transformer)
            if geometry is None:
                measurements.append(self._make_feature_measurement(feature, MeasurementStatus.NOT_APPLICABLE, MeasurementType.NONE))
                continue

            geometry_type = normalise_geometry_type(geometry)
            if geometry_type in ("Polygon", "MultiPolygon"):
                metrics = self._polygon_metrics(geometry, plan)
                measurements.append(
                    self._make_feature_measurement(feature, MeasurementStatus.MEASURED, MeasurementType.AREA, metrics)
                )
            elif geometry_type in ("LineString", "MultiLineString"):
                metrics = self._line_metrics(geometry, plan)
                measurements.append(
                    self._make_feature_measurement(feature, MeasurementStatus.MEASURED, MeasurementType.LENGTH, metrics)
                )
            elif geometry_type == "Point":
                measurements.append(self._make_feature_measurement(feature, MeasurementStatus.NOT_APPLICABLE, MeasurementType.NONE))
            else:
                measurements.append(
                    self._make_feature_measurement(feature, MeasurementStatus.NOT_MEASURABLE, MeasurementType.NONE)
                )
        return measurements

    def _measure_geodesic(self, dataset: ReadDataset, plan: MeasurementPlan) -> list[FeatureMeasurement]:
        """Ellipsoidal length / area with ``pyproj.Geod`` (the only suitable strategy for degrees)."""
        geod = self._geod_for(dataset.source_crs)
        measurement_type = self._infer_type(dataset)

        measurements: list[FeatureMeasurement] = []
        for feature in dataset.features:
            geometry = feature.geometry
            if geometry is None:
                measurements.append(self._make_feature_measurement(feature, MeasurementStatus.NOT_APPLICABLE, MeasurementType.NONE))
                continue

            geometry_type = normalise_geometry_type(geometry)
            if geometry_type in ("LineString", "MultiLineString"):
                metrics = self._line_metrics(geometry, plan, geod)
                measurements.append(
                    self._make_feature_measurement(feature, MeasurementStatus.MEASURED, MeasurementType.LENGTH, metrics)
                )
            elif geometry_type in ("Polygon", "MultiPolygon"):
                metrics = self._polygon_metrics(geometry, plan, geod)
                measurements.append(
                    self._make_feature_measurement(feature, MeasurementStatus.MEASURED, MeasurementType.AREA, metrics)
                )
            elif geometry_type == "Point":
                measurements.append(self._make_feature_measurement(feature, MeasurementStatus.NOT_APPLICABLE, MeasurementType.NONE))
            else:
                measurements.append(
                    self._make_feature_measurement(feature, MeasurementStatus.NOT_MEASURABLE, MeasurementType.NONE)
                )
        return measurements

    def _unmeasured(self, dataset: ReadDataset, reason: str) -> list[FeatureMeasurement]:
        measurements: list[FeatureMeasurement] = []
        for feature in dataset.features:
            measurements.append(
                self._make_feature_measurement(
                    feature,
                    MeasurementStatus.NOT_MEASURABLE,
                    MeasurementType.NONE,
                    error_code="MISSING_CRS",
                    error_message=f"No measurement possible because the source CRS is {reason}.",
                )
            )
        return measurements

    def _make_feature_measurement(
        self,
        feature,
        status: str,
        mtype: str,
        metrics: dict[str, float] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> FeatureMeasurement:
        geometry = feature.geometry
        geometry_type = normalise_geometry_type(geometry)
        geojson = geometry_to_geojson(geometry)
        properties = to_json_safe_mapping(feature.properties)
        fm = FeatureMeasurement(
            feature_id=feature.feature_id,
            geometry_type=geometry_type,
            geometry=geojson,
            properties=properties,
            measurement_status=status,
            measurement_type=mtype,
            error_code=error_code,
            error_message=error_message,
        )
        if metrics:
            if "square_meters" in metrics:
                fm.area_square_meters = metrics.get("square_meters")
            if "meters" in metrics:
                fm.length_meters = metrics.get("meters")
        return fm

    # ------------------------------------------------------------------ #
    # Geometry repair / projection
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reproject_geometry(geometry: Any, transformer: Transformer) -> Any | None:
        """Reproject a single Shapely geometry into a projected plane."""
        if geometry is None:
            return None
        if geometry.is_empty:
            return None

        try:
            # shapely.ops.transform expects a callable returning transformed x,y
            def _project(x, y, z=None):
                return transformer.transform(x, y)
            return shapely_transform(_project, geometry)
        except Exception:
            return geometry

    def _geod_for(self, crs: CRS | None) -> Geod:
        cache_key = crs.to_wkt() if crs is not None else "WGS84"
        geod = self._geod_cache.get(cache_key)
        if geod is None:
            geod = self.crs_service.make_geod(crs)
            self._geod_cache[cache_key] = geod
        return geod

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    @staticmethod
    def _infer_type(dataset: ReadDataset) -> MeasurementType:
        kinds = {normalise_geometry_type(f.geometry) for f in dataset.features if f.geometry is not None}
        if kinds & {"Polygon", "MultiPolygon"}:
            return MeasurementType.AREA
        if kinds & {"LineString", "MultiLineString"}:
            return MeasurementType.LENGTH
        return MeasurementType.NONE

    @staticmethod
    def _polygon_metrics(geometry: Any, plan: MeasurementPlan, geod: Geod | None = None) -> dict[str, float]:
        if plan.uses_planar:
            area = geometry.area
            return {"square_meters": area * plan.unit_to_meters ** 2}
        return {"square_meters": _geodesic_area(geometry, geod)}

    @staticmethod
    def _line_metrics(geometry: Any, plan: MeasurementPlan, geod: Geod | None = None) -> dict[str, float]:
        if plan.uses_planar:
            length = geometry.length
            return {"meters": length * plan.unit_to_meters}
        return {"meters": _geodesic_length(geometry, geod)}

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _persist(self, file_id: str | None, measurements: list[FeatureMeasurement]) -> None:
        for index, measurement in enumerate(measurements):
            measurement.file_id = file_id
            measurement.feature_index = index
        FeatureMeasurement.__table__.insert().execute(measurements)


# --------------------------------------------------------------------------- #
# Low level geodesic helpers
# --------------------------------------------------------------------------- #
def _geodesic_area(geometry: Any, geod: Geod | None = None) -> float:
    """Area on ellipsoid using pyproj.Geod.polygon_area_perimeter."""
    if geod is None:
        geod = CrsService.make_geod(None)
    if geometry is None or geometry.is_empty:
        return 0.0
    if isinstance(geometry, Polygon):
        return abs(_polygon_geodesic_area(geometry, geod))
    if isinstance(geometry, MultiPolygon):
        return sum(abs(_polygon_geodesic_area(p, geod)) for p in geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return sum(_geodesic_area(part, geod) for part in geometry.geoms if part.is_valid)
    return 0.0


def _polygon_geodesic_area(poly: Polygon, geod: Geod) -> float:
    """Signed geodesic area for a single polygon (exterior minus holes)."""
    lons, lats = poly.exterior.coords.xy
    area, _ = geod.polygon_area_perimeter(lons, lats)
    for interior in poly.interiors:
        ilons, ilats = interior.coords.xy
        interior_area, _ = geod.polygon_area_perimeter(ilons, ilats)
        area += interior_area
    return area


def _geodesic_length(geometry: Any, geod: Geod | None = None) -> float:
    """Sum the geodesic distances of every segment in a linestring."""
    if geod is None:
        geod = CrsService.make_geod(None)
    if geometry is None or geometry.is_empty:
        return 0.0
    if isinstance(geometry, LineString):
        return _segment_length_sum(geometry, geod)
    if isinstance(geometry, GeometryCollection):
        return sum(_geodesic_length(part, geod) for part in geometry.geoms if part.is_valid)
    if hasattr(geometry, "geoms"):  # MultiLineString / MultiPolygon
        return sum(_geodesic_length(part, geod) for part in geometry.geoms if part.is_valid)
    return 0.0



def _geodesic_length_2points(a: tuple[float, float], b: tuple[float, float], geod: Geod | None = None) -> float:
    if geod is None:
        geod = CrsService.make_geod(None)
    return geod.inv(a[0], a[1], b[0], b[1])[2]


def _segment_length_sum(geometry: Any, geod: Geod | None = None) -> float:
    total = 0.0
    for i in range(len(geometry.coords) - 1):
        total += _geodesic_length_2points(geometry.coords[i], geometry.coords[i + 1], geod)
    return total
