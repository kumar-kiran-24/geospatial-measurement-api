"""CRS classification and measurement-strategy selection.

Why this module exists
----------------------
Area and length must never be computed by treating longitude/latitude values as
metres: one degree of longitude is ~111 km at the equator and 0 km at the poles,
so a naive planar calculation in degrees is wrong by a factor that varies with
latitude.

Strategy selection (see also README "CRS handling strategy")
-----------------------------------------------------------
============================  =========================================
Source CRS                    Action
============================  =========================================
missing / unparseable         no measurement (``MISSING_CRS``); geometry
                              and attributes are still preserved
projected, linear units       planar measurement in the source CRS, with
                              the result converted to metres
geographic, local extent      planar measurement in the UTM zone that
                              contains the dataset
geographic, wide/polar extent ellipsoidal (geodesic) measurement with
                              ``pyproj.Geod``
unsupported flavour           geodesic measurement, or no measurement
============================  =========================================

When a UTM CRS is synthesised for a non-WGS84 datum, the ellipsoid of the source
is reused explicitly (``+a``/``+rf``) and no datum transformation is performed,
so measurements cannot pick up a silent datum-shift error.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyproj import CRS, Geod, Transformer
# pyproj 3.7 no longer exposes this name in ``pyproj.crs.coordinate_system``.
# Ellipsoidal coordinate systems are detected via the CRS axis/unit metadata
# instead (see :func:`is_geographic_degrees`).

from app.core.logging import get_logger
from app.models.enums import CrsKind, MeasurementStrategy

logger = get_logger(__name__)


def is_geographic_degrees(crs: CRS | None) -> bool:
    """Whether ``crs`` is a geographic CRS whose units are degrees."""
    if crs is None:
        return False
    try:
        axis = crs.axis_info[0] if crs.axis_info else None
        return bool(crs.is_geographic and axis and "degree" in (axis.unit_name or "").lower())
    except Exception:  # noqa: BLE001
        return False


def _is_geographic_ellipsoidal_crs(crs: CRS | None) -> bool:
    """Best-effort check for an ellipsoidal (lon/lat) coordinate system.

    pyproj 3.7 exposes the CS granularity through ``crs.coordinate_system`` and
    reports it as one of the strings ``"ellipsoidal"``, ``"Cartesian"`` or
    ``"vertical"``. This helper lets the legacy ``EllipsoidalCS`` introspection
    be replaced with that public API.
    """
    if crs is None:
        return False
    try:
        granularity = getattr(crs, "coordinate_system", None)
        return str(granularity).lower() == "ellipsoidal"
    except Exception:  # noqa: BLE001
        return False

def axis_is_ellipsoidal(crs: CRS | None) -> bool:
    """Whether the CRS uses an ellipsoidal (lon/lat) coordinate system."""
    return _is_geographic_ellipsoidal_crs(crs)

WGS84_EPSG = 4326
WGS84_UTM_NORTH_BASE = 32600
WGS84_UTM_SOUTH_BASE = 32700

#: UTM is only defined between 80S and 84N.
UTM_MAX_LATITUDE = 84.0
#: A dataset whose extent fits inside a single 6-degree UTM zone (plus some
#: leeway for latitude) is treated as "local".
LOCAL_LONGITUDE_SPAN_DEGREES = 6.0
LOCAL_LATITUDE_SPAN_DEGREES = 6.0


@dataclass(frozen=True)
class MeasurementPlan:
    """How a dataset should be measured."""

    strategy: MeasurementStrategy
    planar_crs: CRS | None = None
    unit_to_meters: float = 1.0
    geodesic_crs: CRS | None = None
    label: str | None = None
    reason: str | None = None

    @property
    def is_measurable(self) -> bool:
        return self.strategy is not MeasurementStrategy.NONE

    @property
    def uses_planar(self) -> bool:
        return self.planar_crs is not None and self.strategy is not MeasurementStrategy.GEODESIC


class CrsService:
    """Classifies CRSs and decides how a dataset should be measured."""

    # ------------------------------------------------------------------ #
    # Classification
    # ------------------------------------------------------------------ #
    @staticmethod
    def classify(crs: CRS | None) -> CrsKind:
        if crs is None:
            return CrsKind.UNKNOWN
        try:
            if crs.is_geographic:
                return CrsKind.GEOGRAPHIC
            if crs.is_projected:
                return CrsKind.PROJECTED
        except Exception:  # noqa: BLE001 - a malformed CRS is simply unknown
            logger.warning("crs_classification_failed")
        return CrsKind.UNKNOWN

    # ------------------------------------------------------------------ #
    # Plan selection
    # ------------------------------------------------------------------ #
    def build_plan(
        self,
        source_crs: CRS | None,
        bounds: tuple[float, float, float, float] | None,
    ) -> MeasurementPlan:
        """Choose the measurement strategy for a whole dataset."""
        if source_crs is None:
            logger.info("measurement_plan_selected", extra={"strategy": "NONE", "reason": "MISSING_CRS"})
            return MeasurementPlan(
                strategy=MeasurementStrategy.NONE, reason="MISSING_CRS"
            )

        kind = self.classify(source_crs)

        if kind is CrsKind.PROJECTED:
            return self._plan_for_projected(source_crs)

        if kind is CrsKind.GEOGRAPHIC:
            return self._plan_for_geographic(source_crs, bounds)

        return MeasurementPlan(
            strategy=MeasurementStrategy.NONE, reason="UNSUPPORTED_CRS"
        )

    def _plan_for_projected(self, source_crs: CRS) -> MeasurementPlan:
        axis = source_crs.axis_info[0] if source_crs.axis_info else None
        unit_name = (axis.unit_name if axis else "") or ""
        factor = float(axis.unit_conversion_factor or 1.0) if axis else 1.0

        # A projected CRS expressed in angular units is not usable for planar
        # measurement; fall back to geodesic measurement on its ellipsoid.
        if "degree" in unit_name.lower() or factor <= 0:
            logger.warning("projected_crs_has_angular_units")
            return self._geodesic_plan(source_crs.geodetic_crs)
        if not axis:
            logger.warning("projected_crs_missing_axis_info", extra={"assumed_unit": "metre"})

        label = self._crs_label(source_crs)
        logger.info(
            "measurement_plan_selected",
            extra={
                "strategy": MeasurementStrategy.PROJECTED_SOURCE.value,
                "measurement_crs": label,
                "unit_to_meters": factor,
            },
        )
        return MeasurementPlan(
            strategy=MeasurementStrategy.PROJECTED_SOURCE,
            planar_crs=source_crs,
            unit_to_meters=factor,
            label=label,
        )

    def _plan_for_geographic(
        self, source_crs: CRS, bounds: tuple[float, float, float, float] | None
    ) -> MeasurementPlan:
        axis = source_crs.axis_info[0] if source_crs.axis_info else None
        unit_name = (axis.unit_name if axis else "") or ""
        if unit_name and "degree" not in unit_name.lower():
            # Grads or radians: do not interpret the numbers as degrees.
            logger.warning("geographic_crs_not_in_degrees", extra={"unit": unit_name})
            return self._geodesic_plan(source_crs)

        if _is_geographic_ellipsoidal_crs(source_crs):
            # The CRS is already a geographic/ellipsoidal CRS: the geometric
            # branches below handle it (UTM for local extents, geodesic otherwise).
            pass

        if bounds is None:
            logger.info("measurement_plan_selected", extra={"strategy": "GEODESIC", "reason": "NO_BOUNDS"})
            return self._geodesic_plan(source_crs)

        min_lon, min_lat, max_lon, max_lat = bounds
        lon_span = abs(max_lon - min_lon)
        lat_span = abs(max_lat - min_lat)
        centre_lon = (min_lon + max_lon) / 2.0
        centre_lat = (min_lat + max_lat) / 2.0

        is_local = (
            lon_span <= LOCAL_LONGITUDE_SPAN_DEGREES
            and lat_span <= LOCAL_LATITUDE_SPAN_DEGREES
            and abs(centre_lat) <= UTM_MAX_LATITUDE
        )

        if not is_local:
            logger.info(
                "measurement_plan_selected",
                extra={
                    "strategy": "GEODESIC",
                    "reason": "EXTENT_SPANS_MULTIPLE_ZONES",
                    "lon_span_degrees": round(lon_span, 4),
                    "lat_span_degrees": round(lat_span, 4),
                },
            )
            return self._geodesic_plan(source_crs)

        utm_crs, label = self._utm_crs(source_crs, centre_lon, centre_lat)
        logger.info(
            "measurement_plan_selected",
            extra={"strategy": MeasurementStrategy.LOCAL_UTM.value, "measurement_crs": label},
        )
        return MeasurementPlan(
            strategy=MeasurementStrategy.LOCAL_UTM,
            planar_crs=utm_crs,
            unit_to_meters=1.0,
            label=label,
        )

    def _geodesic_plan(self, crs: CRS | None) -> MeasurementPlan:
        return MeasurementPlan(
            strategy=MeasurementStrategy.GEODESIC,
            geodesic_crs=crs,
            label=None,
        )

    # ------------------------------------------------------------------ #
    # CRS construction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _crs_label(crs: CRS) -> str:
        epsg = None
        try:
            epsg = crs.to_epsg()
        except Exception:  # noqa: BLE001 - labels are advisory
            epsg = None
        if epsg:
            return f"EPSG:{epsg}"
        return crs.name or "unnamed CRS"

    @staticmethod
    def _utm_crs(geodetic_crs: CRS, lon: float, lat: float) -> tuple[CRS, str]:
        """Build the UTM CRS covering (``lon``, ``lat``) on the source ellipsoid."""
        zone = int((lon + 180.0) / 6.0) + 1
        zone = max(1, min(60, zone))
        south = lat < 0
        hemisphere = "S" if south else "N"

        try:
            if geodetic_crs.to_epsg() == WGS84_EPSG:
                epsg = (WGS84_UTM_SOUTH_BASE if south else WGS84_UTM_NORTH_BASE) + zone
                return CRS.from_epsg(epsg), f"EPSG:{epsg}"
        except Exception:  # noqa: BLE001 - fall through to the explicit build
            pass

        ellipsoid = geodetic_crs.ellipsoid
        params = [f"+proj=utm", f"+zone={zone}"]
        if south:
            params.append("+south")
        params.append(f"+a={ellipsoid.semi_major_metre}")
        if ellipsoid.inverse_flattening:
            params.append(f"+rf={ellipsoid.inverse_flattening}")
        else:  # a sphere
            params.append(f"+b={ellipsoid.semi_minor_metre}")
        params.extend(["+units=m", "+no_defs"])

        label = f"UTM zone {zone}{hemisphere} ({ellipsoid.name})"
        return CRS.from_proj4(" ".join(params)), label

    @staticmethod
    def make_geod(crs: CRS | None) -> Geod:
        """Build a :class:`pyproj.Geod` matching ``crs``'s ellipsoid."""
        if crs is None:
            return Geod(ellps="WGS84")
        try:
            ellipsoid = crs.ellipsoid
            if ellipsoid.inverse_flattening:
                return Geod(a=ellipsoid.semi_major_metre, rf=ellipsoid.inverse_flattening)
            return Geod(a=ellipsoid.semi_major_metre, b=ellipsoid.semi_minor_metre)
        except Exception:  # noqa: BLE001 - fall back to WGS84 explicitly logged
            logger.warning("geod_ellipsoid_unavailable", extra={"fallback": "WGS84"})
            return Geod(ellps="WGS84")

    @staticmethod
    def make_transformer(source_crs: CRS | None, target_crs: CRS | None) -> Transformer | None:
        """Create a transformer, or ``None`` when no transformation is needed."""
        if source_crs is None or target_crs is None:
            return None
        if source_crs.equals(target_crs) or source_crs.is_exact_same(target_crs):
            return None
        return Transformer.from_crs(source_crs, target_crs, always_xy=True)

    @staticmethod
    def is_geographic_degrees(crs: CRS | None) -> bool:
        """Whether ``crs`` is a geographic CRS whose units are degrees."""
        if crs is None:
            return False
        try:
            axis = crs.axis_info[0] if crs.axis_info else None
            return bool(crs.is_geographic and axis and "degree" in (axis.unit_name or "").lower())
        except Exception:  # noqa: BLE001
            return False
