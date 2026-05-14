"""Tabular dataframe serialization and preview helpers."""

from __future__ import annotations

from decimal import Decimal
import io
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List

import numpy as np
import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)

JsonPrimitive = str | int | float | bool | None


def _read_csv_safe(file_path: Path) -> pd.DataFrame:
    """Load a CSV with robust encoding detection."""
    encodings_to_try = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
    last_err = None
    for enc in encodings_to_try:
        try:
            return pd.read_csv(file_path, low_memory=False, encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception:
            raise
    raise UnicodeDecodeError(
        last_err.encoding,
        last_err.object,
        last_err.start,
        last_err.end,
        f"Impossibile decodificare {file_path.name} con nessuno di {encodings_to_try}",
    )


def _compact_whole_number_floats(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize whole-number floats and Decimal values for tabular output."""
    if df is None or df.empty:
        return df

    def _compact_decimal(value: Decimal) -> str:
        if not value.is_finite():
            return str(value)
        text = format(value.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    result = df
    for col in list(result.columns):
        s = result[col]

        if s.dtype == object:
            non_nan = s.dropna()
            if non_nan.empty:
                continue
            if all(isinstance(v, Decimal) for v in non_nan):
                decimals = list(non_nan)
                if all(v == v.to_integral_value() for v in decimals):
                    if result is df:
                        result = df.copy()
                    try:
                        result[col] = pd.array(
                            [
                                int(v) if v is not None and not pd.isna(v) else pd.NA
                                for v in s
                            ],
                            dtype="Int64",
                        )
                    except (TypeError, ValueError):
                        result[col] = s.map(
                            lambda v: None if pd.isna(v) else _compact_decimal(v)
                        )
                else:
                    if result is df:
                        result = df.copy()
                    result[col] = s.map(
                        lambda v: None if pd.isna(v) else _compact_decimal(v)
                    )
                continue

        if pd.api.types.is_float_dtype(s):
            non_nan = s.dropna()
            if not non_nan.empty and (non_nan % 1 == 0).all():
                if result is df:
                    result = df.copy()
                try:
                    result[col] = result[col].astype("Int64")
                except (TypeError, ValueError):
                    continue
    return result


def tabular_dataframe_to_bytes(df: pd.DataFrame, formato: str) -> bytes:
    """Serialize a DataFrame to bytes for non-geospatial formats."""
    df = _compact_whole_number_floats(df)
    if formato == "csv":
        buf = io.StringIO()
        df.to_csv(buf, index=False, encoding="utf-8", float_format="%.15g")
        return buf.getvalue().encode("utf-8")

    if formato == "xlsx":
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue()

    if formato == "json":
        records = sanitize_for_json(df.to_dict(orient="records"))
        return json.dumps(records, indent=2, default=str, ensure_ascii=False).encode("utf-8")

    if formato == "parquet":
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()

    raise ValueError(f"Formato '{formato}' non supportato")


def filter_visible_columns(
    df: pd.DataFrame,
    node_columns: List[Dict[str, Any]],
) -> pd.DataFrame:
    """Filter a DataFrame to visible canvas columns."""
    if not node_columns:
        return df

    visibili = [col for col in node_columns if col.get("visible", True)]
    if not visibili:
        return df

    seen_names = set()
    colonne_da_selezionare = []
    for col in visibili:
        raw_name = col.get("name", "")
        alias = col.get("alias") or raw_name
        if raw_name in df.columns and raw_name not in seen_names:
            colonne_da_selezionare.append((raw_name, alias))
            seen_names.add(raw_name)

    if not colonne_da_selezionare:
        logger.warning("Nessuna colonna visibile trovata nel DataFrame")
        return df

    raw_names = [c[0] for c in colonne_da_selezionare]
    alias_map = {c[0]: c[1] for c in colonne_da_selezionare if c[0] != c[1]}

    result = df[raw_names]
    if alias_map:
        result = result.rename(columns=alias_map)
    return result


def sanitize_for_json(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Make row dictionaries safe for JSON serialization."""
    try:
        from shapely.geometry.base import BaseGeometry

        shapely_disponibile = True
    except ImportError:
        shapely_disponibile = False

    def _sanitizza_valore(value):
        if isinstance(value, np.ndarray):
            return [_sanitizza_valore(v) for v in value.tolist()]

        if isinstance(value, list):
            return [_sanitizza_valore(v) for v in value]

        if isinstance(value, dict):
            return {k: _sanitizza_valore(v) for k, v in value.items()}

        try:
            if value is None or (isinstance(value, float) and np.isnan(value)):
                return None
        except (TypeError, ValueError):
            pass

        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)

        if isinstance(value, (pd.Timestamp, np.datetime64)):
            return str(value)

        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.hex()

        if shapely_disponibile and isinstance(value, BaseGeometry):
            return value.wkt if value and not value.is_empty else None

        return value

    for row in rows:
        for chiave in list(row.keys()):
            row[chiave] = _sanitizza_valore(row[chiave])

    return rows


def _rest_json_safe_value(value: object) -> JsonPrimitive:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and value != value:
            return None
        return value
    return str(value)


def dataframe_to_rest_records(df: pd.DataFrame) -> tuple[dict[str, JsonPrimitive], ...]:
    """Return records safe for generic REST JSON payloads."""
    records: list[dict[str, JsonPrimitive]] = []
    for raw_record in df.to_dict(orient="records"):
        records.append(
            {
                str(key): _rest_json_safe_value(value)
                for key, value in raw_record.items()
            }
        )
    return tuple(records)


def dataframe_preview_records(df: pd.DataFrame, limit: int) -> List[Dict[str, Any]]:
    """Return JSON-safe preview records for a frame-like pandas object."""
    preview_df = df.head(limit)
    return sanitize_for_json(preview_df.to_dict(orient="records"))


def append_record_rows(df: pd.DataFrame, rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Append record dictionaries to a DataFrame preserving legacy concat behavior."""
    if not rows:
        return df
    extra_df = pd.DataFrame(rows)
    return pd.concat([df, extra_df], ignore_index=True)


def concat_dataframes(
    frames: Iterable[Any],
    *,
    ignore_index: bool = False,
    axis: int = 0,
) -> Any:
    """Concatenate frame-like values through the current dataframe engine."""
    return pd.concat(list(frames), ignore_index=ignore_index, axis=axis)


def null_row_indices_for_columns(df: pd.DataFrame, columns: List[str]) -> List[Any]:
    """Return row indices where any of the selected columns is null or empty."""
    if not columns:
        return []
    null_mask = pd.Series(False, index=df.index)
    for col in columns:
        col_null = df[col].isna() | (df[col].astype(str) == "")
        null_mask = null_mask | col_null
    return df.index[null_mask].tolist()


def dataframe_head_records(df: pd.DataFrame, limit: int) -> List[Dict[str, Any]]:
    """Return raw record dictionaries for the first rows of a DataFrame."""
    return df.head(limit).to_dict(orient="records")


def dataframe_from_records(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Build a DataFrame from record dictionaries."""
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def empty_dataframe() -> pd.DataFrame:
    """Build an empty DataFrame."""
    return pd.DataFrame()


def dataframe_from_rows(rows: List[Any], columns: List[str]) -> pd.DataFrame:
    """Build a DataFrame from positional rows and explicit columns."""
    return pd.DataFrame(rows, columns=columns)


def dataframe_from_mapping(
    data: Any = None,
    *,
    index: Any = None,
    columns: List[str] | None = None,
) -> pd.DataFrame:
    """Build a DataFrame from mapping-like data with optional index/columns."""
    return pd.DataFrame(data, index=index, columns=columns)


def is_dataframe(value: Any) -> bool:
    """Return True when value is a DataFrame from the current engine."""
    return isinstance(value, pd.DataFrame)


def dataframe_hash_bytes(
    df: Any,
    *,
    index: bool = False,
    categorize: bool = True,
) -> bytes:
    """Return stable row-hash bytes for the current dataframe engine."""
    row_hashes = pd.util.hash_pandas_object(
        df,
        index=index,
        categorize=categorize,
    ).to_numpy(dtype="uint64", copy=False)
    return row_hashes.tobytes()


def empty_object_series() -> pd.Series:
    """Return an empty object Series preserving pandas assignment semantics."""
    return pd.Series([], dtype=object)


def false_boolean_series(index: Any) -> pd.Series:
    """Return a False boolean Series aligned to a caller-provided index."""
    return pd.Series(False, index=index)


def object_series(values: Any, *, index: Any = None) -> pd.Series:
    """Return an object Series from values, optionally aligned to an index."""
    return pd.Series(values, index=index, dtype=object)


def constant_object_series(value: Any, index: Any) -> pd.Series:
    """Return an object Series filled with one value and aligned to an index."""
    return pd.Series(value, index=index, dtype=object)


def missing_scalar() -> Any:
    """Return the current dataframe engine's missing scalar value."""
    return pd.NA


def dataframe_eval_namespace() -> dict[str, Any]:
    """Return dataframe-engine names intentionally exposed to formula eval."""
    return {"pd": pd}


def coerce_datetime_series(
    values: Any,
    *,
    errors: str = "coerce",
    format: str | None = None,
    dayfirst: bool = False,
) -> Any:
    """Convert values to pandas datetime with invalid entries coerced."""
    kwargs: dict[str, Any] = {"errors": errors}
    if format:
        kwargs["format"] = format
    if dayfirst:
        kwargs["dayfirst"] = dayfirst
    return pd.to_datetime(values, **kwargs)


def coerce_numeric_series(values: Any, *, errors: str = "coerce") -> Any:
    """Convert values to numeric with invalid entries coerced."""
    return pd.to_numeric(values, errors=errors)


def cut_numeric_series(
    values: Any,
    *,
    bins: Any,
    labels: Any = None,
    include_lowest: bool = True,
) -> Any:
    """Coerce values to numeric and bin them using the current dataframe engine."""
    numeric_values = pd.to_numeric(values, errors="coerce")
    return pd.cut(
        numeric_values,
        bins=bins,
        labels=labels,
        include_lowest=include_lowest,
    )


def normalize_json_records(records: List[Dict[str, Any]], max_level: int = 1) -> Any:
    """Normalize JSON-like record dictionaries into a flat DataFrame."""
    return pd.json_normalize(records, max_level=max_level)


def merge_dataframes(
    left: Any,
    right: Any,
    **kwargs: Any,
) -> Any:
    """Merge frame-like values through the current dataframe engine."""
    return pd.merge(left, right, **kwargs)


def pivot_table_dataframe(
    data: Any,
    **kwargs: Any,
) -> Any:
    """Create a pivot table through the current dataframe engine."""
    return pd.pivot_table(data, **kwargs)


def melt_dataframe(
    df: Any,
    **kwargs: Any,
) -> Any:
    """Unpivot a DataFrame through the current dataframe engine."""
    return pd.melt(df, **kwargs)


def join_columns_as_strings(df: pd.DataFrame, separator: str) -> Any:
    """Join column values as strings preserving legacy pandas coercion."""
    return df.fillna("").astype(str).agg(separator.join, axis=1)


def is_numeric_series(values: Any) -> bool:
    """Return True when values have a pandas numeric dtype."""
    return bool(pd.api.types.is_numeric_dtype(values))


def is_bool_series(values: Any) -> bool:
    """Return True when values have a pandas bool dtype."""
    return bool(pd.api.types.is_bool_dtype(values))


def is_float_series(values: Any) -> bool:
    """Return True when values have a pandas float dtype."""
    return bool(pd.api.types.is_float_dtype(values))


def is_multiindex(value: Any) -> bool:
    """Return True when value is a pandas MultiIndex."""
    return isinstance(value, pd.MultiIndex)


def drop_rows_reset_index(df: pd.DataFrame, row_indices: Iterable[Any]) -> pd.DataFrame:
    """Drop rows by index and reset to the default positional index."""
    return df.drop(index=list(row_indices), errors="ignore").reset_index(drop=True)


def iter_dataframe_rows(df: pd.DataFrame) -> Iterator[Any]:
    """Yield pandas row Series preserving legacy iterrows semantics."""
    for _, row in df.iterrows():
        yield row


__all__ = [
    "append_record_rows",
    "_compact_whole_number_floats",
    "_read_csv_safe",
    "concat_dataframes",
    "cut_numeric_series",
    "dataframe_from_records",
    "dataframe_from_mapping",
    "dataframe_from_rows",
    "dataframe_eval_namespace",
    "dataframe_hash_bytes",
    "dataframe_preview_records",
    "dataframe_head_records",
    "dataframe_to_rest_records",
    "coerce_datetime_series",
    "coerce_numeric_series",
    "drop_rows_reset_index",
    "empty_dataframe",
    "empty_object_series",
    "false_boolean_series",
    "filter_visible_columns",
    "is_bool_series",
    "is_dataframe",
    "is_float_series",
    "is_multiindex",
    "is_numeric_series",
    "iter_dataframe_rows",
    "join_columns_as_strings",
    "constant_object_series",
    "melt_dataframe",
    "merge_dataframes",
    "missing_scalar",
    "normalize_json_records",
    "null_row_indices_for_columns",
    "object_series",
    "pivot_table_dataframe",
    "sanitize_for_json",
    "tabular_dataframe_to_bytes",
]
