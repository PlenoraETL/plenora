"""Tabular dataframe serialization and preview helpers."""

from __future__ import annotations

from decimal import Decimal
import io
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Iterable, Iterator, List, Optional

import numpy as np
import pandas as pd

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.shared.contracts.payloads import IntMap, JsonObject, JsonRows, StringMap
from app.utils.logger import get_logger

logger = get_logger(__name__)

JsonPrimitive = str | int | float | bool | None
RestRecord = Mapping[str, JsonPrimitive]
RestRecords = tuple[RestRecord, ...]


_CSV_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


def _read_csv_with_encodings(
    source: "Path | BytesIO",
    *,
    label: str,
) -> pd.DataFrame:
    """Helper interno: pd.read_csv con fallback multi-encoding.

    `source` puo' essere un `Path` (legge da filesystem) o un
    `io.BytesIO` (legge da blob in memoria). In entrambi i casi tenta
    la chain `utf-8 / utf-8-sig / cp1252 / latin-1` — pre-fix il path
    blob in `frame_io._load_dataframe_from_bytes` non aveva fallback
    e produceva dati corrotti o exception su CSV italiani cp1252.
    """
    from io import BytesIO as _BytesIO  # local import to avoid module cycle
    last_err: Optional[UnicodeDecodeError] = None
    for enc in _CSV_ENCODINGS:
        try:
            if isinstance(source, _BytesIO):
                source.seek(0)
            return pd.read_csv(source, low_memory=False, encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception:
            raise
    raise UnicodeDecodeError(
        last_err.encoding if last_err else "utf-8",
        last_err.object if last_err else b"",
        last_err.start if last_err else 0,
        last_err.end if last_err else 0,
        f"Impossibile decodificare {label} con nessuno di {_CSV_ENCODINGS}",
    )


def _read_csv_safe(file_path: Path) -> pd.DataFrame:
    """Load a CSV from filesystem with robust encoding detection."""
    return _read_csv_with_encodings(file_path, label=file_path.name)


def read_csv_safe_bytes(data: bytes, *, source_label: str = "in-memory CSV") -> pd.DataFrame:
    """Load a CSV from raw bytes with robust encoding detection.

    Esportata per il path blob (`frame_io._load_dataframe_from_bytes`):
    pre-fix `pd.read_csv(BytesIO(data))` non specificava `encoding=` e
    falliva silenziosamente su CSV italiani cp1252 (P1 plenora-bug-
    etl-sources). Allinea il blob path al pattern del filesystem path
    via lo stesso helper.
    """
    from io import BytesIO
    return _read_csv_with_encodings(BytesIO(data), label=source_label)


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
                        result = copy_frame_for_update(df)
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
                        result = copy_frame_for_update(df)
                    result[col] = s.map(
                        lambda v: None if pd.isna(v) else _compact_decimal(v)
                    )
                continue

        if pd.api.types.is_float_dtype(s):
            non_nan = s.dropna()
            if not non_nan.empty and (non_nan % 1 == 0).all():
                if result is df:
                    result = copy_frame_for_update(df)
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
        # Pre-fix: `df.to_parquet(buf, index=False)` senza `engine=` esplicito.
        # Pandas usa l'engine "auto" che, su install dove sia fastparquet
        # sia pyarrow sono presenti (o solo fastparquet), puo' degradare
        # Int64 nullable a float64 silenziosamente nel round-trip — vedi
        # fix D1 in sessione precedente che esplicitava `dtype_backend=
        # "numpy_nullable"` sul read path. Allineiamo il write path con
        # engine="pyarrow" esplicito: pyarrow e' un dep diretto di
        # requirements.txt e gestisce Int64/Float64 nullable nativamente.
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, engine="pyarrow")
        return buf.getvalue()

    raise ValueError(f"Formato '{formato}' non supportato")


def make_unique_column_names(
    columns: Iterable[Any],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Return stable, public-safe column names.

    Duplicate names are suffixed with ``_2``, ``_3``, ... while preserving
    order. Existing names are reserved up front, so ``["id", "id", "id_2"]``
    becomes ``["id", "id_3", "id_2"]`` instead of reintroducing a collision.
    """
    original = [str(col) for col in columns]
    reserved = set(original)
    used: set[str] = set()
    counters: dict[str, int] = {}
    unique: list[str] = []
    renames: list[tuple[str, str]] = []

    for name in original:
        if name not in used:
            used.add(name)
            counters.setdefault(name, 1)
            unique.append(name)
            continue

        suffix = counters.get(name, 1)
        while True:
            suffix += 1
            candidate = f"{name}_{suffix}"
            if candidate not in used and candidate not in reserved:
                break
        counters[name] = suffix
        used.add(candidate)
        unique.append(candidate)
        renames.append((name, candidate))

    return unique, renames


def rename_duplicate_columns(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """Rename duplicate DataFrame columns without dropping data."""
    unique_columns, renames = make_unique_column_names(df.columns)
    if not renames:
        return df, []
    renamed = copy_frame_for_update(df)
    renamed.columns = unique_columns
    return renamed, renames


def filter_visible_columns(
    df: pd.DataFrame,
    node_columns: JsonRows,
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

    # `.copy()` esplicito per evitare view-vs-copy ambiguity. Pre-fix:
    # `df[raw_names]` con CoW disabilitato (default pandas < 2.0 e alcuni
    # deploy attuali) ritornava una view. Qualunque modifica downstream
    # sul `result` propagava `SettingWithCopyWarning` o mutava silenzio-
    # samente il `df` originale. Il `rename(columns=...)` non risolveva
    # perche' opera sullo stesso oggetto. La copia ha costo O(rows) ma
    # qui i caller sono pipeline nodes che gia' fanno copie a valle.
    result = df[raw_names].copy()
    if alias_map:
        result = result.rename(columns=alias_map)
    return result


def sanitize_for_json(rows: JsonRows) -> JsonRows:
    """Make row dictionaries safe for JSON serialization.

    Contract (vedi `tests2/dataframes/unit/test_tabular_no_in_place_mutation_contract.py`):
    la funzione NON muta `rows` ne' i dict interni. Il caller puo'
    riutilizzare l'oggetto originale dopo la chiamata. Pre-fix l'iterazione
    `row[chiave] = _sanitizza_valore(row[chiave])` mutava in-place ogni
    dict di input, corrompendo lo stato del caller (ad es. preview che
    usava i rows due volte vedeva la seconda volta valori gia' "stringa").
    """
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

    # List-comprehension di nuovi dict: niente mutazione in-place dei dict
    # passati dal caller.
    return [
        {chiave: _sanitizza_valore(valore) for chiave, valore in row.items()}
        for row in rows
    ]


def _rest_json_safe_value(value: object) -> JsonPrimitive:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and value != value:
            return None
        return value
    return str(value)


def dataframe_to_rest_records(df: pd.DataFrame) -> RestRecords:
    """Return records safe for generic REST JSON payloads."""
    records: list[RestRecord] = []
    for raw_record in df.to_dict(orient="records"):
        records.append(
            {
                str(key): _rest_json_safe_value(value)
                for key, value in raw_record.items()
            }
        )
    return tuple(records)


def dataframe_preview_records(df: pd.DataFrame, limit: int) -> JsonRows:
    """Return JSON-safe preview records for a frame-like pandas object."""
    preview_df = df.head(limit)
    return sanitize_for_json(preview_df.to_dict(orient="records"))


def dataframe_records(df: pd.DataFrame) -> JsonRows:
    """Return JSON-safe record dictionaries for a full frame-like object."""
    return sanitize_for_json(df.to_dict(orient="records"))


def dataframe_row_dict(df: pd.DataFrame, index: Any) -> JsonObject:
    """Return a row dictionary by dataframe index."""
    return df.loc[index].to_dict()


def dataframe_row_by_index(df: pd.DataFrame, index: Any) -> Any:
    """Return a row object by dataframe index."""
    return df.loc[index]


def dataframe_first_row_dict(df: pd.DataFrame) -> JsonObject:
    """Return the first row as a dictionary."""
    return df.iloc[0].to_dict()


def dataframe_first_column(df: pd.DataFrame) -> Any:
    """Return the first column from a dataframe-like object."""
    return df.iloc[:, 0]


def dataframe_column_by_position(df: pd.DataFrame, index: int) -> Any:
    """Return a column by positional index."""
    return df.iloc[:, index]


def iter_dataframe_indexed_rows(df: pd.DataFrame) -> Iterator[tuple[Any, Any]]:
    """Yield ``(index, row)`` pairs preserving current engine row semantics."""
    for idx, row in df.iterrows():
        yield idx, row


def set_cell_value(df: pd.DataFrame, index: Any, column: str, value: Any) -> None:
    """Set one dataframe cell through the current engine."""
    df.at[index, column] = value


def set_values_where_mask(
    df: pd.DataFrame,
    mask: Any,
    column: str,
    value: Any,
) -> None:
    """Set a column value for rows selected by a boolean mask."""
    df.loc[mask, column] = value


def sample_dataframe(
    df: pd.DataFrame,
    *,
    n: int,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Sample rows through the current dataframe engine."""
    return df.sample(n=n, random_state=random_state)


def sample_rows_reset_index(
    df: pd.DataFrame,
    *,
    n: int | None = None,
    frac: float | None = None,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Sample rows and reset to a default positional index."""
    if frac is not None:
        return df.sample(frac=frac, random_state=random_state).reset_index(drop=True)
    return df.sample(n=n, random_state=random_state).reset_index(drop=True)


def null_count_for_columns(df: pd.DataFrame, columns: Iterable[str]) -> int:
    """Count null values across existing columns."""
    total = 0
    for col in columns:
        if col in df.columns:
            total += int(df[col].isna().sum())
    return total


def duplicate_row_count(df: pd.DataFrame, subset: Iterable[str]) -> int:
    """Count duplicate rows for the selected subset, excluding first hits."""
    duplicated = df.duplicated(subset=list(subset), keep="first")
    return int(duplicated.sum())


def unique_non_null_column_values(df: pd.DataFrame, column: str) -> List[Any]:
    """Return unique non-null values for one column."""
    return df[column].dropna().unique().tolist()


def duplicate_non_null_value_count(df: pd.DataFrame, column: str) -> int:
    """Count duplicated non-null values in one column."""
    return int(df[column].duplicated(keep=False).sum() - df[column].isna().sum())


def filter_rows_by_column_predicate(
    df: pd.DataFrame,
    column: str,
    predicate: Any,
) -> pd.DataFrame:
    """Filter rows by applying a predicate to a column."""
    return df[df[column].apply(predicate)]


def row_indices_where_column_in(
    df: pd.DataFrame,
    column: str,
    values: Iterable[Any],
) -> List[Any]:
    """Return row indices whose column value is in the given value set."""
    return df[df[column].isin(set(values))].index.tolist()


def unique_rows_for_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Return unique rows for a column subset."""
    return df[list(columns)].drop_duplicates()


def drop_duplicate_rows_reset_index(
    df: pd.DataFrame,
    *,
    subset: Iterable[str] | None = None,
    keep: str | bool = "first",
) -> pd.DataFrame:
    """Drop duplicate rows and reset to a default positional index."""
    subset_list = list(subset) if subset is not None else None
    return df.drop_duplicates(subset=subset_list, keep=keep).reset_index(drop=True)


def drop_duplicate_columns(df: pd.DataFrame, *, keep: str = "first") -> pd.DataFrame:
    """Drop duplicate-named columns preserving the selected occurrence."""
    return df.loc[:, ~df.columns.duplicated(keep=keep)]


def sort_rows_reset_index(
    df: pd.DataFrame,
    *,
    by: str | Iterable[str],
    ascending: bool = True,
) -> pd.DataFrame:
    """Sort rows and reset to a default positional index."""
    return df.sort_values(by=by, ascending=ascending).reset_index(drop=True)


def group_size_reset(
    df: pd.DataFrame,
    group_by: Iterable[str],
    *,
    name: str = "count",
    dropna: bool = False,
) -> pd.DataFrame:
    """Group rows and return group sizes as a normal dataframe."""
    return df.groupby(list(group_by), dropna=dropna).size().reset_index(name=name)


def group_aggregate_reset(
    df: pd.DataFrame,
    group_by: Iterable[str],
    agg_dict: JsonObject,
    *,
    dropna: bool = False,
) -> pd.DataFrame:
    """Group rows and aggregate through the current dataframe engine."""
    return df.groupby(list(group_by), dropna=dropna).agg(agg_dict).reset_index()


def group_aggregate_as_columns(
    df: pd.DataFrame,
    group_by: Iterable[str],
    agg_dict: JsonObject,
) -> pd.DataFrame:
    """Group rows with ``as_index=False`` and aggregate columns."""
    return df.groupby(list(group_by), as_index=False).agg(agg_dict)


def group_keys_dataframe(df: pd.DataFrame, group_by: Iterable[str]) -> pd.DataFrame:
    """Return one row per group key as a normal dataframe."""
    return df.groupby(list(group_by), as_index=False).size().drop(columns=["size"])


def group_size_values(df: pd.DataFrame, group_by: Iterable[str]) -> Any:
    """Return size values for groups in current engine order."""
    return df.groupby(list(group_by)).size().values


def iter_grouped_dataframes(df: pd.DataFrame, by: str) -> Iterator[tuple[Any, pd.DataFrame]]:
    """Yield grouped dataframe pairs through the current engine."""
    for key, group in df.groupby(by):
        yield key, group


def group_named_aggregate_merge(
    df: pd.DataFrame,
    group_by: str,
    agg_dict: JsonObject,
    *,
    how: str = "left",
    dropna: bool = False,
) -> pd.DataFrame:
    """Compute named group aggregations and merge them back to the frame."""
    grouped = df.groupby(group_by, dropna=dropna).agg(**agg_dict)
    return df.merge(grouped, on=group_by, how=how)


def group_cumcount_series(df: pd.DataFrame, partition_column: str, start: int = 1) -> Any:
    """Return a one-based cumulative row counter within each group."""
    return df.groupby(partition_column).cumcount() + start


def window_function_values(
    df: pd.DataFrame,
    column: str,
    function: str,
    *,
    group_by: str | None = None,
    offset: int = 1,
) -> Any:
    """Compute supported window-function values for one column."""
    if group_by and group_by in df.columns:
        grouped = df.groupby(group_by)
        if function == "rank":
            return grouped[column].rank(method="min")
        if function == "dense_rank":
            return grouped[column].rank(method="dense")
        if function == "cumsum":
            return grouped[column].cumsum()
        if function == "cumcount":
            return grouped.cumcount()
        if function == "lag":
            return grouped[column].shift(offset)
        if function == "lead":
            return grouped[column].shift(-offset)
        if function == "pct_change":
            return grouped[column].pct_change()
        if function == "running_mean":
            return grouped[column].expanding().mean().reset_index(level=0, drop=True)

    if function == "rank":
        return df[column].rank(method="min")
    if function == "dense_rank":
        return df[column].rank(method="dense")
    if function == "cumsum":
        return df[column].cumsum()
    if function == "cumcount":
        return range(len(df))
    if function == "lag":
        return df[column].shift(offset)
    if function == "lead":
        return df[column].shift(-offset)
    if function == "pct_change":
        return df[column].pct_change()
    if function == "running_mean":
        return df[column].expanding().mean()
    raise ValueError(f"Unsupported window function: {function}")


def series_null_ratio(values: Any) -> float:
    """Return null ratio for a series-like value."""
    return float(values.isna().sum()) / max(len(values), 1)


def estimate_merge_output_rows(
    left: pd.DataFrame,
    right: pd.DataFrame,
    join_keys: Iterable[str],
    how: str,
) -> int | None:
    """Estimate equality-merge output rows for supported join types."""
    join_how = str(how or "inner").lower()
    if join_how not in {"inner", "left", "right", "outer"}:
        return None
    keys = list(join_keys)
    if not keys:
        return None

    left_counts = left.groupby(keys, dropna=False).size()
    right_counts = right.groupby(keys, dropna=False).size()

    matched_keys = left_counts.index.intersection(right_counts.index)
    if len(matched_keys):
        left_matched = left_counts.loc[matched_keys]
        right_matched = right_counts.loc[matched_keys]
        matched_rows = int((left_matched * right_matched).sum())
        left_matched_input_rows = int(left_matched.sum())
        right_matched_input_rows = int(right_matched.sum())
    else:
        matched_rows = 0
        left_matched_input_rows = 0
        right_matched_input_rows = 0

    left_unmatched_rows = len(left) - left_matched_input_rows
    right_unmatched_rows = len(right) - right_matched_input_rows

    if join_how == "inner":
        return matched_rows
    if join_how == "left":
        return matched_rows + left_unmatched_rows
    if join_how == "right":
        return matched_rows + right_unmatched_rows
    return matched_rows + left_unmatched_rows + right_unmatched_rows


def matching_row_indices(
    df: pd.DataFrame,
    match_values: JsonObject,
    *,
    limit: int | None = None,
) -> List[Any]:
    """Return indices whose row values match all criteria, including nulls."""
    mask = pd.Series(True, index=df.index)
    for col, value in match_values.items():
        try:
            is_null = bool(pd.isna(value))
        except (TypeError, ValueError):
            is_null = False
        if is_null:
            mask &= df[col].isna()
        else:
            mask &= df[col] == value
    indices = df[mask].index.tolist()
    return indices if limit is None else indices[:limit]


def non_null_head_values(values: Any, limit: int) -> List[Any]:
    """Return the first non-null values from a series-like object."""
    return list(values.dropna().head(limit))


def mixed_numeric_string_object_columns(
    df: pd.DataFrame,
    *,
    sample_size: int = 1000,
) -> List[str]:
    """Find object columns mixing numeric and string values."""
    mixed_columns: List[str] = []
    for col in df.columns:
        if df[col].dtype != "object":
            continue
        sample = non_null_head_values(df[col], sample_size)
        if not sample:
            continue
        has_numeric = False
        has_string = False
        for val in sample:
            if isinstance(val, (int, float)):
                has_numeric = True
            elif isinstance(val, str):
                has_string = True
            if has_numeric and has_string:
                mixed_columns.append(col)
                break
    return mixed_columns


def stringify_columns_preserving_nulls(
    df: pd.DataFrame,
    columns: Iterable[str],
) -> pd.DataFrame:
    """Convert selected columns to strings while preserving null sentinels.

    Contract: il DataFrame di input NON viene mutato. Pre-fix il loop
    scriveva `df[col] = df[col].astype(str)` direttamente sull'oggetto
    ricevuto, corrompendo lo stato del caller (es. executor che ancora
    aveva un riferimento al df pre-stringify per altre operazioni).
    """
    out = df.copy()
    for col in columns:
        mask_null = out[col].isna()
        out[col] = out[col].astype(str)
        out.loc[mask_null, col] = None
    return out


def geometry_series_columns_to_wkt(df: pd.DataFrame) -> pd.DataFrame:
    """Convert GeoSeries-like columns to WKT strings.

    Contract: il DataFrame di input NON viene mutato (vedi
    `stringify_columns_preserving_nulls` per lo stesso pattern).

    Delega la serializzazione per-colonna a
    `geometry_serialization.geometry_series_to_wkt` (vettoriale via
    `shapely.to_wkt`, ~2.4x sui punti / ~1.3-1.7x sui poligoni vs
    `apply(lambda g: g.wkt ...)` per-riga). Normalizza tutti i
    missing (None/NaN/pd.NA) a None — pre-fix il lambda restituiva
    None solo per i missing che passavano il check `is_null_scalar`,
    lasciando potenziali NaN nell'output.
    """
    from app.core.dataframes.values import is_geometry_series
    from app.core.dataframes.geometry_serialization import (
        geometry_series_to_wkt,
    )

    out = copy_frame_for_update(df)
    for col in out.columns:
        if is_geometry_series(out[col]):
            out[col] = geometry_series_to_wkt(out[col])
    return out


def select_and_rename_columns(
    df: pd.DataFrame,
    columns: Iterable[str],
    *,
    rename_map: StringMap | None = None,
) -> pd.DataFrame:
    """Select existing columns and optionally rename them."""
    selected = [col for col in columns if col in df.columns]
    result = df[selected]
    if rename_map:
        applicable = {
            old_name: new_name
            for old_name, new_name in rename_map.items()
            if old_name in result.columns
        }
        if applicable:
            result = result.rename(columns=applicable)
    return result


def append_record_rows(df: pd.DataFrame, rows: JsonRows) -> pd.DataFrame:
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


def dataframe_head_records(df: pd.DataFrame, limit: int) -> JsonRows:
    """Return raw record dictionaries for the first rows of a DataFrame."""
    return df.head(limit).to_dict(orient="records")


def dataframe_from_records(rows: JsonRows) -> pd.DataFrame:
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


def dataframe_eval_namespace() -> JsonObject:
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
    kwargs: JsonObject = {"errors": errors}
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


def normalize_json_records(records: JsonRows, max_level: int = 1) -> Any:
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


def concat_non_null_as_strings(values: Any, separator: str = ", ") -> str:
    """Concatenate non-null series values as strings."""
    return separator.join(values.dropna().astype(str))


def table_diff_dataframe(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
    *,
    key_cols: list[str],
    common_value_cols: list[str],
    only_old_cols: list[str],
    only_new_cols: list[str],
    include_unchanged: str = "no",
    separator: str = "#",
) -> tuple[pd.DataFrame, IntMap]:
    """Vectorized table-diff implementation for MANIPOLA."""
    merged = old_df.merge(
        new_df,
        on=key_cols,
        how="outer",
        suffixes=("_OLD", "_NEW"),
        indicator=True,
    )

    merge_type = merged["_merge"]
    mask_deleted = merge_type == "left_only"
    mask_added = merge_type == "right_only"
    mask_both = merge_type == "both"

    changed_masks = {}
    for col in common_value_cols:
        old_s = merged[f"{col}_OLD"]
        new_s = merged[f"{col}_NEW"]
        both_nan = old_s.isna() & new_s.isna()
        one_nan = old_s.isna() ^ new_s.isna()
        if is_numeric_series(old_s) and is_numeric_series(new_s):
            numeric_diff = (old_s.fillna(0) - new_s.fillna(0)).abs() > 1e-9
            col_changed = one_nan | (numeric_diff & ~both_nan)
        else:
            str_diff = old_s.astype(str) != new_s.astype(str)
            col_changed = one_nan | (str_diff & ~both_nan & ~one_nan)
        changed_masks[col] = col_changed

    changed_df = (
        dataframe_from_mapping(changed_masks, index=merged.index)
        if common_value_cols
        else dataframe_from_mapping(index=merged.index)
    )

    any_changed = (
        changed_df.any(axis=1)
        if len(changed_df.columns)
        else false_boolean_series(merged.index)
    )
    status = constant_object_series("UNCHANGED", merged.index)
    status[mask_deleted] = "DELETED"
    status[mask_added] = "ADDED"
    status[mask_both & any_changed] = "MODIFIED"

    if include_unchanged != "yes":
        keep = status != "UNCHANGED"
        merged = merged[keep]
        changed_df = changed_df.loc[keep] if len(changed_df.columns) else changed_df
        status = status[keep]

    if len(merged) == 0:
        columns = key_cols + common_value_cols + [
            "_diff_status",
            "_diff_columns",
            "_diff_old_values",
        ]
        return dataframe_from_rows([], columns), {}

    result_df = dataframe_from_mapping(index=merged.index)
    for key_col in key_cols:
        result_df[key_col] = merged[key_col]

    is_deleted = (status == "DELETED").values
    for col in common_value_cols:
        old_col = f"{col}_OLD"
        new_col = f"{col}_NEW"
        result_df[col] = np.where(is_deleted, merged[old_col], merged[new_col])
    for col in only_old_cols:
        if col in merged.columns:
            result_df[col] = merged[col]
    for col in only_new_cols:
        if col in merged.columns:
            result_df[col] = merged[col]

    diff_columns_col = object_series([None] * len(merged), index=merged.index)
    diff_old_values_col = object_series([None] * len(merged), index=merged.index)

    modified_mask = status == "MODIFIED"
    if modified_mask.any() and common_value_cols:
        cd_mod = changed_df.loc[modified_mask]
        stacked = cd_mod.stack()
        trues = stacked[stacked]
        if len(trues) > 0:
            diff_cols_per_row = (
                trues.reset_index(level=1)
                .groupby(level=0)[trues.index.names[1] or "level_1"]
                .agg(separator.join)
                .reindex(cd_mod.index, fill_value="")
            )
            diff_columns_col.loc[modified_mask] = diff_cols_per_row.values

            old_values_df = dataframe_from_mapping(index=cd_mod.index)
            for col in common_value_cols:
                old_values = merged.loc[modified_mask, f"{col}_OLD"]
                old_values_df[col] = old_values.where(old_values.notna(), "").astype(str)

            old_stacked = old_values_df.stack()
            old_at_changes = old_stacked.loc[trues.index]
            diff_old_per_row = (
                old_at_changes.groupby(level=0)
                .agg(separator.join)
                .reindex(cd_mod.index, fill_value="")
            )
            diff_old_values_col.loc[modified_mask] = diff_old_per_row.values

    result_df["_diff_status"] = status.values
    result_df["_diff_columns"] = diff_columns_col.values
    result_df["_diff_old_values"] = diff_old_values_col.values
    result_df = result_df.reset_index(drop=True)
    status_counts = result_df["_diff_status"].value_counts().to_dict()
    return result_df, status_counts


def is_numeric_series(values: Any) -> bool:
    """Return True when values have a pandas numeric dtype."""
    return bool(pd.api.types.is_numeric_dtype(values))


def is_bool_series(values: Any) -> bool:
    """Return True when values have a pandas bool dtype."""
    return bool(pd.api.types.is_bool_dtype(values))


def is_float_series(values: Any) -> bool:
    """Return True when values have a pandas float dtype."""
    return bool(pd.api.types.is_float_dtype(values))


def float_series_non_null_whole_numbers(values: Any) -> bool:
    """Return True when all non-null float values are whole numbers."""
    non_null = values.dropna()
    return bool(not non_null.empty and (non_null % 1 == 0).all())


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
    "concat_non_null_as_strings",
    "cut_numeric_series",
    "dataframe_column_by_position",
    "dataframe_from_records",
    "dataframe_from_mapping",
    "dataframe_from_rows",
    "dataframe_eval_namespace",
    "dataframe_hash_bytes",
    "dataframe_preview_records",
    "dataframe_head_records",
    "dataframe_first_column",
    "dataframe_first_row_dict",
    "dataframe_records",
    "dataframe_row_by_index",
    "dataframe_row_dict",
    "dataframe_to_rest_records",
    "coerce_datetime_series",
    "coerce_numeric_series",
    "duplicate_row_count",
    "duplicate_non_null_value_count",
    "drop_duplicate_columns",
    "drop_duplicate_rows_reset_index",
    "drop_rows_reset_index",
    "empty_dataframe",
    "empty_object_series",
    "false_boolean_series",
    "filter_visible_columns",
    "filter_rows_by_column_predicate",
    "float_series_non_null_whole_numbers",
    "geometry_series_columns_to_wkt",
    "group_aggregate_as_columns",
    "group_aggregate_reset",
    "group_keys_dataframe",
    "group_cumcount_series",
    "group_size_reset",
    "group_size_values",
    "group_named_aggregate_merge",
    "is_bool_series",
    "is_dataframe",
    "is_float_series",
    "is_multiindex",
    "is_numeric_series",
    "iter_grouped_dataframes",
    "iter_dataframe_indexed_rows",
    "iter_dataframe_rows",
    "join_columns_as_strings",
    "make_unique_column_names",
    "constant_object_series",
    "estimate_merge_output_rows",
    "matching_row_indices",
    "melt_dataframe",
    "merge_dataframes",
    "missing_scalar",
    "mixed_numeric_string_object_columns",
    "non_null_head_values",
    "normalize_json_records",
    "null_count_for_columns",
    "null_row_indices_for_columns",
    "object_series",
    "pivot_table_dataframe",
    "sample_dataframe",
    "sample_rows_reset_index",
    "sanitize_for_json",
    "select_and_rename_columns",
    "rename_duplicate_columns",
    "row_indices_where_column_in",
    "set_cell_value",
    "set_values_where_mask",
    "sort_rows_reset_index",
    "series_null_ratio",
    "stringify_columns_preserving_nulls",
    "tabular_dataframe_to_bytes",
    "table_diff_dataframe",
    "unique_non_null_column_values",
    "unique_rows_for_columns",
    "window_function_values",
]
