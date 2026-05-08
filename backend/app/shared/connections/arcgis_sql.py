"""ArcGIS SQL literal helpers shared by provider adapters.

Keeps provider-generic WHERE construction out of feature and IMDF domains.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence


def _is_missing_value(value: object) -> bool:
    if value is None:
        return True
    try:
        comparison = value != value
    except (TypeError, ValueError):
        return False
    return isinstance(comparison, bool) and comparison


def _to_python_scalar(value: object) -> object:
    package = type(value).__module__.split(".", 1)[0]
    if package in {"numpy", "pandas"} and hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, TypeError, ValueError):
            return value
    return value


def quote_arcgis_sql_value(value: object) -> str:
    """Quote a Python value for an ArcGIS SQL WHERE literal."""
    if _is_missing_value(value):
        return "NULL"

    value = _to_python_scalar(value)

    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "NULL" if value != value else repr(value)

    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def chunk_arcgis_values(
    values: Sequence[object],
    size: int,
) -> Iterator[Sequence[object]]:
    """Yield ordered ArcGIS-safe value batches."""
    if size <= 0:
        raise ValueError(f"chunk_arcgis_values: size must be > 0, got {size}")
    for index in range(0, len(values), size):
        yield values[index : index + size]
