"""Geometry <-> GeoJSON helpers.

Coordinates are rounded to a fixed precision before serialisation (7 decimal
places is roughly 1 cm at the equator, which is far below the accuracy of any
real survey dataset) to keep the JSONB payloads compact without discarding
meaningful information.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

#: Decimal places kept for stored/served coordinates.
DEFAULT_COORDINATE_PRECISION = 7


def _round_coordinates(value: Any, precision: int) -> Any:
    if isinstance(value, float):
        return round(value, precision)
    if isinstance(value, (list, tuple)):
        return [_round_coordinates(item, precision) for item in value]
    return value


def geometry_to_geojson(
    geometry: BaseGeometry | None,
    precision: int = DEFAULT_COORDINATE_PRECISION,
) -> dict[str, Any] | None:
    """Return a GeoJSON mapping for ``geometry`` with rounded coordinates."""
    if geometry is None or geometry.is_empty:
        return None
    payload = mapping(geometry)
    payload["coordinates"] = _round_coordinates(payload.get("coordinates"), precision)
    return payload


def normalise_geometry_type(geometry: BaseGeometry | None) -> str:
    """Return a stable geometry type label for ``geometry``.

    ``None`` yields ``"None"`` (the dataset declared a geometry column but the
    feature carries no geometry) which keeps the value distinct from an
    unsupported-but-present type.
    """
    if geometry is None:
        return "None"
    return geometry.geom_type
