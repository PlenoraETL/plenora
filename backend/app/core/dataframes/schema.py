"""Dataframe-backed column schema inference."""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Mapping, Optional

import pandas as pd

from app.core.resources.column_types import (
    normalize_canvas_type,
)
from app.shared.contracts.payloads import StringMap
from app.core.dataframes.tabular import (
    float_series_non_null_whole_numbers,
    non_null_head_values,
)
from app.shared.execution.column_info import ColumnInfo
from app.shared.execution.schema_column_plans import type_family

_logger = logging.getLogger(__name__)


def record_column_names(labels: Iterable[str | None]) -> list[str]:
    """Etichette dei record, inclusa la coercizione pandas None/nan."""
    labels = list(labels)
    row = dict.fromkeys(labels)
    normalized = dict(zip(row, map(str, pd.DataFrame([row]).columns)))
    return [normalized[label] for label in labels]


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
        sample = pd.Series(non_null_head_values(series, 10))
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
        values = non_null_head_values(series, 10)
        if not values:
            return "str"

        sample = str(values[0])

        sample_upper = sample.strip().upper()
        if any(sample_upper.startswith(prefix) for prefix in cls.WKT_PREFIXES):
            return "geometry"

        if len(sample) >= 8 and any(c in sample for c in ["-", "/"]):
            if cls._looks_like_date_column(pd.Series(values)):
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


def typed_probe_frame(column: str, canvas_type: Optional[str], values: List[Any]) -> pd.DataFrame:
    """Frame di una colonna del tipo ``canvas_type`` con i valori dati.

    Serve alle previsioni che ESEGUONO l'operazione su ingressi rappresentativi
    (Fase 5, T6, BA1: tipi del Lookup). ``None`` e' il null nella forma che
    quella famiglia ha davvero nei dati caricati: NaN per i numeri (una
    colonna intera con null arriva come float64), NaT per le date, None per
    testo e booleani (object).
    """
    dtype = canvas_type_to_pandas_dtype(canvas_type)
    has_null = any(value is None for value in values)
    if dtype == "int64" and has_null:
        dtype = "float64"
    if dtype == "bool" and has_null:
        dtype = "object"
    if dtype.startswith("datetime64"):
        series = pd.Series(pd.to_datetime(values), dtype=dtype)
    else:
        series = pd.Series(values, dtype=dtype)
    return pd.DataFrame({column: series})


def canvas_type_to_pandas_dtype(canvas_type: Optional[str]) -> str:
    """Return a pandas dtype string for a canvas column type.

    Regola unica: il tipo si riconduce alla sua FAMIGLIA con la stessa tabella
    usata dall'inferenza (``type_families.json``: integer, number, string,
    datetime, timestamp, alias dei database...), poi famiglia -> dtype. Prima
    i tipi del contratto e dell'inferenza fuori dal vocabolario minimo
    (``datetime``, ``number``, ``string``...) diventavano ``object`` e
    l'esecuzione a vuoto di Valida rifiutava configurazioni valide (es. una
    Formula con ``.dt`` su una colonna datetime).
    """
    return _FAMILY_DTYPES.get(type_family(canvas_type), "object")


# Famiglia -> dtype pandas. "str": dtype stringa predefinito del motore
# (pandas 3), lo stesso che build_columns_with_types riporta come "str".
_FAMILY_DTYPES = {
    "int": "int64",
    "real": "float64",
    "bool": "bool",
    "date": "datetime64[ns]",
    "datetime": "datetime64[ns]",
    "str": "str",
}


def series_to_canvas_type(series: Any) -> str:
    """Infer the canonical canvas type for a frame-engine series.

    Dispatch based on `series.dtype` plus the N11 heuristic for recovering
    integers promoted to float by NaN.
    """
    dtype_str = str(series.dtype).lower()

    if "int" in dtype_str:
        return "int"
    if "float" in dtype_str:
        if float_series_non_null_whole_numbers(series):
            return "int"
        return "real"
    if "bool" in dtype_str:
        return "bool"
    if "datetime" in dtype_str:
        return "date"
    if dtype_str == "object":
        sample = non_null_head_values(series, 20)
        if not sample:
            return "str"
        all_numeric = True
        for value in sample:
            try:
                float(value)
            except (TypeError, ValueError):
                all_numeric = False
                break
        if all_numeric:
            return "real"
        return "str"
    return "str"


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


def resolve_column_type(
    *, runtime_dtype: Any = None, declared: Optional[str] = None,
    inferred: Optional[str] = None, series: Any = None,
) -> str:
    """Specific runtime dtype > declaration > content recognition > string."""
    if series is not None:
        runtime_dtype = series.dtype
        inferred = TypeInferrer.infer_column_type(series)
    dtype = str(runtime_dtype if runtime_dtype is not None else "").lower()
    if "int" in dtype:
        return "int"
    if "float" in dtype:
        return "real"
    if "bool" in dtype:
        return "bool"
    if "datetime" in dtype:
        return "date"
    if dtype == "geometry":
        return "geometry"
    # object/str non provano un tipo logico. Una dichiarazione resta utile
    # sui null, ma non puo trasformare contenuto testuale reale incompatibile.
    if series is not None and inferred in {"str", "string"}:
        values = series.dropna()
        if not values.empty and all(isinstance(value, str) for value in values):
            return "str"
    for candidate in (declared, inferred):
        if candidate and str(candidate).lower() not in {"object", "unknown", "none"}:
            return candidate
    return "str"
