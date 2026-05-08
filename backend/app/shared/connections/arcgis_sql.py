"""ArcGIS SQL literal helpers shared by provider adapters.

Keeps provider-generic WHERE construction out of feature and IMDF domains.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence


def quote_arcgis_sql_value(value: object) -> str:
    """Quote a Python value for an ArcGIS SQL WHERE literal."""
    try:
        import pandas as _pd

        if value is None or _pd.isna(value):
            return "NULL"
    except (TypeError, ValueError):
        if value is None:
            return "NULL"

    try:
        import numpy as _np

        if isinstance(value, (bool, _np.bool_)):
            return "1" if bool(value) else "0"
        if isinstance(value, _np.integer):
            return str(int(value))
        if isinstance(value, _np.floating):
            float_value = float(value)
            if float_value != float_value:
                return "NULL"
            return repr(float_value)
    except ImportError:
        if isinstance(value, bool):
            return "1" if value else "0"

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
