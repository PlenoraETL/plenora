"""Dataframe-backed column schema inference."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional

import pandas as pd

from app.core.resources.column_types import (
    normalize_canvas_type,
    pandas_series_to_canvas_type as _pandas_series_to_canvas_type,
)
from app.shared.contracts.payloads import StringMap

_logger = logging.getLogger(__name__)

_LOGICAL_TYPE_COMPATIBLE_DTYPES: dict[str, tuple[str, ...]] = {
    "string": ("object", "string", "category"),
    "integer": (
        "int8", "int16", "int32", "int64",
        "Int8", "Int16", "Int32", "Int64",
        "uint8", "uint16", "uint32", "uint64",
        "UInt8", "UInt16", "UInt32", "UInt64",
    ),
    "number": (
        "float16", "float32", "float64",
        "Float32", "Float64",
        "int8", "int16", "int32", "int64",
        "Int8", "Int16", "Int32", "Int64",
        "uint8", "uint16", "uint32", "uint64",
        "UInt8", "UInt16", "UInt32", "UInt64",
    ),
    "boolean": ("bool", "boolean", "object"),
    "datetime": ("object",),
    "geometry": ("object", "geometry"),
}


try:
    from app.core.projects.canvas_models import ColumnInfo as _AppColumnInfo
    _USE_APP_MODELS = True
except ImportError:  # pragma: no cover
    _USE_APP_MODELS = False

if _USE_APP_MODELS:
    ColumnInfo = _AppColumnInfo
else:
    @dataclass
    class ColumnInfo:  # type: ignore[no-redef]
        """Informazioni su una colonna (fallback dataclass)."""

        name: str
        type: str


class TypeInferrer:
    """Inferisce tipi colonne."""

    WKT_PREFIXES = (
        "POINT",
        "LINESTRING",
        "POLYGON",
        "MULTIPOINT",
        "MULTILINESTRING",
        "MULTIPOLYGON",
        "GEOMETRYCOLLECTION",
    )

    GEOMETRY_TYPE_MAP = {
        "point": "geometry:point",
        "multipoint": "geometry:multipoint",
        "linestring": "geometry:line",
        "multilinestring": "geometry:multiline",
        "polygon": "geometry:polygon",
        "multipolygon": "geometry:multipolygon",
    }

    @classmethod
    def infer_column_type(cls, series: pd.Series) -> str:
        """Inferisce tipo colonna da pandas Series."""
        dtype = str(series.dtype)

        if "int" in dtype:
            return "int"
        if "float" in dtype:
            return "real"
        if "bool" in dtype:
            return "bool"
        if "datetime" in dtype:
            return "date"
        if "geometry" in dtype.lower():
            return "geometry"
        return cls._infer_from_values(series)

    _DATE_FORMATS = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%d-%m-%Y",
    )
    _DATE_PARSE_THRESHOLD = 0.8

    @classmethod
    def _looks_like_date_column(cls, series: pd.Series) -> bool:
        """Conservative date detection using explicit formats only."""
        sample = series.dropna().head(10)
        if sample.empty:
            return False
        n = len(sample)
        threshold = max(1, int(n * cls._DATE_PARSE_THRESHOLD))
        for fmt in cls._DATE_FORMATS:
            parsed = pd.to_datetime(sample, format=fmt, errors="coerce")
            if parsed.notna().sum() >= threshold:
                return True
        return False

    @classmethod
    def _infer_from_values(cls, series: pd.Series) -> str:
        """Inferisce tipo da valori."""
        if series.dropna().empty:
            return "str"

        sample = str(series.dropna().iloc[0])

        sample_upper = sample.strip().upper()
        if any(sample_upper.startswith(prefix) for prefix in cls.WKT_PREFIXES):
            return "geometry"

        if len(sample) >= 8 and any(c in sample for c in ["-", "/"]):
            if cls._looks_like_date_column(series):
                return "date"

        return "str"

    @classmethod
    def detect_geometry_type(cls, geom_types: List[str]) -> str:
        """Mappa tipo geometria."""
        if not geom_types:
            return "geometry"

        geom_type = geom_types[0].lower()
        return cls.GEOMETRY_TYPE_MAP.get(geom_type, "geometry")


class ColumnInfoBuilder:
    """Costruisce info colonne - separato per testabilita'."""

    @staticmethod
    def from_dataframe(
        df: pd.DataFrame,
        gdf: Any = None,
        user_srid: Optional[int] = None,
    ) -> List[ColumnInfo]:
        """Estrae info colonne da DataFrame."""
        columns = []
        geom_columns = {}

        if gdf is not None:
            geom_columns = ColumnInfoBuilder._extract_geometry_columns(gdf, user_srid)

        for col in df.columns:
            if col in geom_columns:
                col_type = geom_columns[col]
            else:
                col_type = TypeInferrer.infer_column_type(df[col])
                if col_type == "geometry" and user_srid:
                    col_type = f"geometry:{user_srid}"

            columns.append(ColumnInfo(name=col, type=col_type))

        return columns

    @staticmethod
    def _extract_geometry_columns(gdf: Any, user_srid: Optional[int]) -> StringMap:
        """Estrae colonne geometriche da GeoDataFrame."""
        geom_columns = {}

        for col in gdf.columns:
            try:
                col_dtype = str(gdf[col].dtype)

                if col_dtype == "geometry" or hasattr(gdf[col], "geom_type"):
                    geom_series = gdf[col]
                    if not geom_series.empty:
                        geom_types = geom_series.dropna().apply(
                            lambda g: g.geom_type if g else None
                        ).dropna().unique().tolist()
                        geom_type = TypeInferrer.detect_geometry_type(geom_types)
                    else:
                        geom_type = "geometry"

                    if gdf.crs:
                        srid = gdf.crs.to_epsg() or user_srid or 4326
                        geom_columns[col] = f"{geom_type}:{srid}"
                    elif user_srid:
                        geom_columns[col] = f"{geom_type}:{user_srid}"
                    else:
                        geom_columns[col] = geom_type
            except Exception as exc:
                _logger.debug(f"geometry detect fallita per colonna '{col}': {exc}")

        return geom_columns


def empty_dataframe_from_column_metadata(
    source_columns: Iterable[Mapping[str, Any]] | None,
) -> pd.DataFrame:
    """Build an empty DataFrame preserving canvas-declared column dtypes."""
    columns_data = {
        col_info.get("name", ""): pd.Series(
            dtype=canvas_type_to_pandas_dtype(col_info.get("type", "str"))
        )
        for col_info in (source_columns or [])
    }
    return pd.DataFrame(columns_data)


def canvas_type_to_pandas_dtype(canvas_type: Optional[str]) -> str:
    """Return a pandas dtype string for a canvas column type."""
    canonical = normalize_canvas_type(canvas_type)
    if canonical == "int":
        return "int64"
    if canonical == "real":
        return "float64"
    if canonical == "bool":
        return "bool"
    if canonical == "date":
        return "datetime64[ns]"
    return "object"


def series_to_canvas_type(series: Any) -> str:
    """Infer the canonical canvas type for a frame-engine series."""
    return _pandas_series_to_canvas_type(series)


def dtype_compatible_with_logical(dtype: Any, logical: str) -> bool:
    """Return True when a frame-engine dtype satisfies a contract logical type."""
    if logical == "datetime":
        try:
            if pd.api.types.is_datetime64_any_dtype(dtype):
                return True
        except (TypeError, ValueError):
            pass
    if logical == "geometry":
        dtype_name = getattr(dtype, "name", str(dtype))
        if dtype_name == "geometry":
            return True
    compatible = _LOGICAL_TYPE_COMPATIBLE_DTYPES.get(logical)
    if not compatible:
        return True
    return str(dtype) in compatible


__all__ = [
    "ColumnInfo",
    "ColumnInfoBuilder",
    "TypeInferrer",
    "canvas_type_to_pandas_dtype",
    "dtype_compatible_with_logical",
    "empty_dataframe_from_column_metadata",
    "series_to_canvas_type",
]
