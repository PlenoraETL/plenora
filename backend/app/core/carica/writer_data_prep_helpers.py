"""
Small data-preparation helpers for DatabaseWriter.

The mixin keeps the public DatabaseWriter method surface; this module owns
the dataframe/value transformations without depending on writer state.
"""

from __future__ import annotations

import json as _json

from typing import Any, Dict, List, Optional, Sequence

from app.core.carica.models import ColumnMapping
from app.core.dataframes.tabular import (
    geometry_series_columns_to_wkt,
    mixed_numeric_string_object_columns,
    stringify_columns_preserving_nulls,
)
from app.core.dataframes.values import is_null_scalar
from app.shared.contracts.payloads import JsonObject


def is_pandas_null(value: Any) -> bool:
    """Return True for pandas null-like scalar values, excluding strings/bytes."""
    if isinstance(value, (str, bytes)):
        return False
    return is_null_scalar(value)


def fix_mixed_type_columns(df: Any, logger) -> Any:
    """Force object columns mixing numeric and string values to strings."""
    mixed_columns = mixed_numeric_string_object_columns(df)
    for col in mixed_columns:
        logger.info(
            f"Colonna '{col}' contiene tipi misti (numeri + stringhe), "
            f"conversione a TEXT"
        )
    stringify_columns_preserving_nulls(df, mixed_columns)

    return df


def convert_geoseries_columns_to_wkt(df: Any, logger) -> Any:
    """Convert GeoSeries columns to WKT when geopandas is available."""
    return geometry_series_columns_to_wkt(df)


def prepare_dataframe(df: Any, logger) -> Any:
    """Prepare a DataFrame for database writes without writer state."""
    prepared = df.copy()
    prepared = fix_mixed_type_columns(prepared, logger)
    return convert_geoseries_columns_to_wkt(prepared, logger)


def geometry_to_wkt(value: Any, logger) -> Optional[str]:
    """Convert common geometry representations to WKT."""
    if value is None:
        return None

    if isinstance(value, str):
        val_upper = value.strip().upper()
        if any(
            val_upper.startswith(prefix)
            for prefix in ("POINT", "LINESTRING", "POLYGON", "MULTI", "GEOMETRY")
        ):
            return value.strip()
        if value.strip().startswith("{"):
            try:
                from shapely.geometry import shape

                return shape(_json.loads(value)).wkt
            except Exception as e:
                logger.warning(f"GeoJSON->WKT fallito, provo WKB: {e}")
        try:
            from shapely import wkb

            geom = wkb.loads(bytes.fromhex(value))
            return geom.wkt
        except Exception as wkb_err:
            logger.debug(
                "[_geometry_to_wkt] parse WKB fallito dopo GeoJSON, "
                "restituisco valore raw: %s: %s",
                type(wkb_err).__name__,
                wkb_err,
            )
            return value

    if hasattr(value, "wkt"):
        return value.wkt

    return str(value)


def normalize_sql_value(value: Any) -> Any:
    """Normalize pandas/numpy/datetime scalar values for SQLAlchemy params."""
    if is_pandas_null(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def build_params(
    row: Any,
    columns: List[ColumnMapping],
    operation: str,
    match_columns: Sequence[str],
    logger,
) -> JsonObject:
    """Build SQLAlchemy parameters for one DataFrame row."""
    params: Dict[str, Any] = {}

    for col in columns:
        value = row.get(col.source_column)
        param_name = col.dest_column

        if is_pandas_null(value):
            params[param_name] = None
            continue

        if col.is_geometry:
            params[param_name] = geometry_to_wkt(value, logger)
            continue

        params[param_name] = normalize_sql_value(value)

    if operation == "update":
        for match_col in match_columns:
            params[f"match_{match_col}"] = normalize_sql_value(row.get(match_col))

    return params
