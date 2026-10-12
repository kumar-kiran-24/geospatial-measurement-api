"""Coercion of arbitrary attribute values into JSON-serialisable data.

Source files (especially Shapefiles) can carry dates, decimals, byte strings
and NaN values that ``json`` cannot serialise. Rather than dropping them, they
are converted to a stable representation so no source attribute is silently
lost.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import math
from typing import Any

#: Keys whose values are never persisted/logged verbatim.
_MAX_STRING_LENGTH = 10_000


def to_json_safe(value: Any) -> Any:
    """Return a JSON-serialisable representation of ``value``."""
    if value is None:
        return None

    # ``bool`` must be checked before ``int`` (bool is a subclass of int).
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN/Infinity are not valid JSON; represent them as null.
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:_MAX_STRING_LENGTH]

    # numpy scalars / arrays expose ``item`` or ``tolist``; duck-type them so
    # this module does not need to import numpy.
    item = getattr(value, "item", None)
    if callable(item) and getattr(value, "shape", None) == ():
        return to_json_safe(item())
    tolist = getattr(value, "tolist", None)
    if callable(tolist) and hasattr(value, "shape"):
        return to_json_safe(tolist())

    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}

    return str(value)[:_MAX_STRING_LENGTH]


def to_json_safe_mapping(mapping: Any) -> dict[str, Any]:
    """Coerce a feature's attribute mapping into a JSON-safe ``dict``."""
    if mapping is None:
        return {}
    if isinstance(mapping, dict):
        return {str(key): to_json_safe(value) for key, value in mapping.items()}
    # GeoPandas/pyogrio may hand back an object with ``items``/``keys``.
    try:
        return {str(key): to_json_safe(value) for key, value in dict(mapping).items()}
    except Exception:  # noqa: BLE001 - attributes are not worth failing over
        return {}
