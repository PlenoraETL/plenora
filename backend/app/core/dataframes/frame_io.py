"""
Path and byte DataFrame I/O helpers.

This module intentionally owns pandas-oriented loading, tabular saving,
column filtering, JSON-safe row normalization, and geospatial file dispatch.
"""

import time as _time_mod

import json
import shutil
import tempfile
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.tabular import (
    _compact_whole_number_floats,
    _read_csv_safe,
    append_record_rows,
    concat_dataframes,
    concat_non_null_as_strings,
    constant_object_series,
    coerce_datetime_series,
    coerce_numeric_series,
    cut_numeric_series,
    dataframe_column_by_position,
    dataframe_from_mapping,
    dataframe_from_records,
    dataframe_from_rows,
    dataframe_eval_namespace,
    dataframe_first_column,
    dataframe_first_row_dict,
    dataframe_hash_bytes,
    dataframe_head_records,
    empty_dataframe,
    dataframe_preview_records,
    dataframe_records,
    dataframe_row_by_index,
    dataframe_row_dict,
    iter_dataframe_rest_records,
    duplicate_non_null_value_count,
    duplicate_row_count,
    drop_duplicate_columns,
    drop_duplicate_rows_reset_index,
    drop_rows_reset_index,
    empty_object_series,
    false_boolean_series,
    filter_rows_by_column_predicate,
    filter_visible_columns,
    float_series_non_null_whole_numbers,
    geometry_series_columns_to_wkt,
    group_aggregate_as_columns,
    group_aggregate_reset,
    group_cumcount_series,
    group_keys_dataframe,
    group_named_aggregate_merge,
    group_size_reset,
    group_size_values,
    is_bool_series,
    is_dataframe,
    is_float_series,
    is_multiindex,
    is_numeric_series,
    iter_grouped_dataframes,
    iter_dataframe_indexed_rows,
    iter_dataframe_rows,
    join_columns_as_strings,
    make_unique_column_names,
    estimate_merge_output_rows,
    matching_row_indices,
    melt_dataframe,
    merge_dataframes,
    missing_scalar,
    mixed_numeric_string_object_columns,
    non_null_head_values,
    normalize_json_records,
    null_count_for_columns,
    null_row_indices_for_columns,
    object_series,
    pivot_table_dataframe,
    rename_duplicate_columns,
    sample_dataframe,
    sample_rows_reset_index,
    sanitize_for_json,
    select_and_rename_columns,
    row_indices_where_column_in,
    set_cell_value,
    set_values_where_mask,
    sort_rows_reset_index,
    series_null_ratio,
    stringify_columns_preserving_nulls,
    tabular_dataframe_to_bytes,
    table_diff_dataframe,
    unique_non_null_column_values,
    unique_rows_for_columns,
    window_function_values,
)
from app.core.resources.exceptions import MemoryLimitExceededError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_GEOSPATIAL_FORMATS = {"geojson", "gpkg", "shp"}
# Tutti i formati supportati ora sono RAM-feasible grazie a pyogrio
# 0.12+ con `read_dataframe(bytes)` e GDAL 3.13.0 unified
# (/vsimem + /vsizip gestiti internamente da pyogrio).
# `_PATH_BOUND_ARTIFACT_FORMATS` resta per back-compat semantica con
# i caller che testano `fmt in _PATH_BOUND_ARTIFACT_FORMATS`, ma il
# branch di lettura ora va sempre attraverso `_load_dataframe_from_bytes`.
_IN_MEMORY_ARTIFACT_FORMATS = {
    "csv", "json", "xlsx", "xls", "parquet", "geojson",
    "gpkg", "geopackage", "shp", "zip", "gdb",
}
_PATH_BOUND_ARTIFACT_FORMATS = {"gpkg", "geopackage", "shp", "zip", "gdb"}

__all__ = [
    "_compact_whole_number_floats",
    "_read_csv_safe",
    "concat_dataframes",
    "concat_non_null_as_strings",
    "constant_object_series",
    "coerce_datetime_series",
    "coerce_numeric_series",
    "cut_numeric_series",
    "dataframe_column_by_position",
    "dataframe_from_mapping",
    "filter_rows_by_column_predicate",
    "filter_visible_columns",
    "float_series_non_null_whole_numbers",
    "find_latest_output_file",
    "load_arcgis_feature_dataframe",
    "load_dataframe_from_artifact",
    "load_dataframe_from_file",
    "dataframe_records",
    "dataframe_first_column",
    "dataframe_first_row_dict",
    "dataframe_row_by_index",
    "dataframe_row_dict",
    "duplicate_non_null_value_count",
    "duplicate_row_count",
    "drop_duplicate_columns",
    "drop_duplicate_rows_reset_index",
    "null_count_for_columns",
    "null_row_indices_for_columns",
    "geometry_series_columns_to_wkt",
    "group_aggregate_as_columns",
    "group_aggregate_reset",
    "group_cumcount_series",
    "group_keys_dataframe",
    "group_named_aggregate_merge",
    "group_size_reset",
    "group_size_values",
    "estimate_merge_output_rows",
    "matching_row_indices",
    "make_unique_column_names",
    "mixed_numeric_string_object_columns",
    "non_null_head_values",
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
    "unique_non_null_column_values",
    "unique_rows_for_columns",
    "window_function_values",
    "DefaultFrameFileIO",
    "append_record_rows",
    "dataframe_from_records",
    "dataframe_from_rows",
    "dataframe_eval_namespace",
    "dataframe_hash_bytes",
    "dataframe_head_records",
    "dataframe_preview_records",
    "iter_dataframe_rest_records",
    "drop_rows_reset_index",
    "empty_dataframe",
    "empty_object_series",
    "false_boolean_series",
    "is_bool_series",
    "is_dataframe",
    "is_float_series",
    "is_multiindex",
    "is_numeric_series",
    "iter_grouped_dataframes",
    "iter_dataframe_indexed_rows",
    "iter_dataframe_rows",
    "join_columns_as_strings",
    "melt_dataframe",
    "merge_dataframes",
    "missing_scalar",
    "normalize_json_records",
    "save_dataframe_to_bytes",
    "object_series",
    "pivot_table_dataframe",
    "tabular_dataframe_to_bytes",
    "table_diff_dataframe",
]


class DefaultFrameFileIO:
    """Default path/bytes I/O adapter for callers that want an injectable port."""

    def load_dataframe_from_file(
        self,
        file_path: Union[str, Path],
        sheet: Optional[str] = None,
        layer: Optional[str] = None,
    ) -> pd.DataFrame:
        return load_dataframe_from_file(file_path, sheet=sheet, layer=layer)

    def save_dataframe_to_bytes(
        self,
        df: pd.DataFrame,
        format: str = "csv",
        geom_column: Optional[str] = None,
        crs: str = "EPSG:4326",
    ) -> bytes:
        return save_dataframe_to_bytes(df, format, geom_column, crs)


def _normalize_format_hint(format_hint: Optional[str]) -> Optional[str]:
    if not format_hint:
        return None
    normalized = str(format_hint).strip().lower().lstrip(".")
    if normalized == "geopackage":
        return "gpkg"
    return normalized or None


def _coerce_json_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce object-dtype columns to numeric when 100% convertibili.

    Contract: NON muta il DataFrame di input. Pre-fix `df[col] = converted`
    scriveva sull'oggetto del caller (allineato al pattern P0
    `stringify_columns_preserving_nulls` gia' fixato in sessione 2).
    Solo i caller `_load_json_dataframe_from_text` + `load_dataframe_from_file`
    si aspettano il DF coerced; copia conservativa qui evita di
    sorprendere consumer futuri.
    """
    out = df
    cols_to_convert = []
    for col in df.columns:
        if df[col].dtype != "object":
            continue
        sample = df[col].dropna().head(100).astype(str)
        has_underscore = sample.str.contains("_", na=False).any()
        if has_underscore:
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        original_count = df[col].notna().sum()
        converted_count = converted.notna().sum()
        if original_count > 0 and converted_count == original_count:
            cols_to_convert.append((col, converted))

    if cols_to_convert:
        # Lazy copy: solo se ci sono modifiche reali, evita allocazione
        # di un DF intero quando nessuna colonna e' candidate.
        out = copy_frame_for_update(df)
        for col, converted in cols_to_convert:
            out[col] = converted
    return out


def _load_json_dataframe_from_text(text: str) -> pd.DataFrame:
    data = json.loads(text)
    return _coerce_json_dataframe_columns(pd.DataFrame(data))


def _load_dataframe_from_bytes(
    data: bytes,
    *,
    format_hint: str,
    sheet: Optional[str] = None,
    layer: Optional[str] = None,
) -> pd.DataFrame:
    if format_hint == "csv":
        # Allinea il path blob al path filesystem (_read_csv_safe) che
        # gia' faceva fallback multi-encoding. Pre-fix
        # `pd.read_csv(BytesIO(data))` senza `encoding=` falliva su CSV
        # italiani cp1252 nel path in-memory.
        from app.core.dataframes.tabular import read_csv_safe_bytes
        return read_csv_safe_bytes(data, source_label="blob CSV")
    if format_hint == "json":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8-sig")
        return _load_json_dataframe_from_text(text)
    if format_hint in {"xlsx", "xls"}:
        stream = BytesIO(data)
        return pd.read_excel(stream, sheet_name=sheet) if sheet else pd.read_excel(stream)
    if format_hint == "parquet":
        # Pre-fix: `pd.read_parquet(BytesIO(data))` senza dtype_backend
        # forzava NumPy classico, e una colonna parquet `int64` con
        # null veniva degradata a `float64` (con NaN al posto dei null)
        # — round-trip non lossless per tipi integer + null.
        # Post-fix: dtype_backend="numpy_nullable" instrada le colonne
        # integer nullable su pandas Int64 e preserva i null come pd.NA.
        # Sicuro per i consumer attuali: `sanitize_for_json` rileva
        # pd.NA via `pd.isna(value)` prima dei branch `isinstance
        # np.integer/np.floating/np.bool_`, e `to_dict("records")`
        # converte gia' Int64/Float64 in scalari Python nativi
        # (int/float) per i valori non-null (vedi audit D1).
        return pd.read_parquet(BytesIO(data), dtype_backend="numpy_nullable")
    if format_hint == "geojson":
        import geopandas as gpd

        try:
            return gpd.read_file(BytesIO(data))
        except Exception:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8-sig")
            return gpd.read_file(StringIO(text))
    if format_hint in _PATH_BOUND_ARTIFACT_FORMATS:
        # Pre-fix: scriveva i bytes su NamedTemporaryFile e poi
        # delegava a `load_dataframe_from_file(tmp_path)`. Costoso
        # (disk write + unlink) e fragile (cleanup race su crash).
        # Post-fix: pyogrio 0.12+ accetta nativamente `bytes` /
        # `BytesIO` come `path_or_buffer` e gestisce internamente
        # /vsimem (per single-file) e /vsizip (per shapefile/gdb
        # ZIP). I formati gpkg/shp/zip/gdb sono ora RAM-only.
        from app.core.io.geospatial import read_vector_bytes
        return read_vector_bytes(data, layer=layer)
    raise ValueError(f"Formato artifact non supportato: {format_hint}")


def load_dataframe_from_artifact(
    *,
    path: Union[str, Path, None] = None,
    data: Optional[bytes] = None,
    format_hint: Optional[str] = None,
    sheet: Optional[str] = None,
    layer: Optional[str] = None,
) -> pd.DataFrame:
    """Load a DataFrame from artifact payload plus explicit format metadata.

    Result blobs carry `results.format`; callers must not infer the format from
    an extensionless tempfile name.
    """
    fmt = _normalize_format_hint(format_hint)
    if data is not None and fmt in (_IN_MEMORY_ARTIFACT_FORMATS | _PATH_BOUND_ARTIFACT_FORMATS):
        return _load_dataframe_from_bytes(data, format_hint=fmt, sheet=sheet, layer=layer)

    if path is None:
        raise ValueError("Artifact senza path o data")

    artifact_path = Path(path)
    if fmt is None:
        return load_dataframe_from_file(artifact_path, sheet=sheet, layer=layer)

    if fmt == "csv":
        return _read_csv_safe(artifact_path)
    if fmt in {"xlsx", "xls"}:
        return pd.read_excel(artifact_path, sheet_name=sheet) if sheet else pd.read_excel(artifact_path)
    if fmt == "json":
        try:
            with open(artifact_path, "r", encoding="utf-8") as f:
                return _load_json_dataframe_from_text(f.read())
        except UnicodeDecodeError:
            with open(artifact_path, "r", encoding="utf-8-sig") as f:
                return _load_json_dataframe_from_text(f.read())
    if fmt == "parquet":
        # Pre-fix / Post-fix: vedi nota in `_load_dataframe_from_bytes`.
        # Stessa policy applicata al path artifact-by-path per uniformare
        # il contratto integer-null-preserving su entrambi i call site.
        return pd.read_parquet(artifact_path, dtype_backend="numpy_nullable")
    if fmt == "geojson":
        import geopandas as gpd

        return gpd.read_file(artifact_path)
    if fmt in _PATH_BOUND_ARTIFACT_FORMATS:
        # Pre-fix: se il file su disco aveva un suffisso "sbagliato"
        # (es. `.bin` per un GPKG), copiava su NamedTemporaryFile con
        # il suffisso corretto perche' `load_dataframe_from_file`
        # dispatcha per estensione. Post-fix: leggi bytes una volta e
        # delega a `_load_dataframe_from_bytes` con `format_hint`
        # esplicito -- niente piu' dipendenza dal suffisso del path.
        # Eccezione: `gdb` puo' essere una DIRECTORY (`.gdb/`), non
        # un file singolo: lascia il dispatch path-based al
        # `load_dataframe_from_file` quando il path e' una dir.
        if fmt == "gdb" and artifact_path.is_dir():
            return load_dataframe_from_file(artifact_path, sheet=sheet, layer=layer)
        return _load_dataframe_from_bytes(
            artifact_path.read_bytes(),
            format_hint=fmt,
            sheet=sheet,
            layer=layer,
        )

    return load_dataframe_from_file(artifact_path, sheet=sheet, layer=layer)


def load_dataframe_from_file(
    file_path: Union[str, Path],
    sheet: Optional[str] = None,
    layer: Optional[str] = None,
) -> pd.DataFrame:
    """Load a DataFrame from tabular or spatial file formats."""
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File non trovato: {file_path}")

    try:
        _preflight_size = file_path.stat().st_size
    except OSError:
        _preflight_size = -1
    if _preflight_size >= 0:
        from app.config import settings as _settings

        _cap = int(_settings.get("LOAD_FILE_MAX_BYTES", 200 * 1024 * 1024))
        if _cap > 0 and _preflight_size > _cap:
            raise MemoryLimitExceededError(
                observed_bytes=_preflight_size,
                limit_bytes=_cap,
                path_basename=file_path.name,
                stage="load_dataframe_from_file",
            )

    estensione = file_path.suffix.lower()


    _t0 = _time_mod.monotonic()
    try:
        try:
            file_size = file_path.stat().st_size
        except OSError:
            file_size = -1
        if estensione == ".csv":
            df = _read_csv_safe(file_path)

        elif estensione in [".xlsx", ".xls"]:
            df = pd.read_excel(file_path, sheet_name=sheet) if sheet else pd.read_excel(file_path)

        elif estensione == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            # Pre-fix: loop in-place che mutava direttamente `df[col] =
            # converted` con logica duplicata vs `_coerce_json_dataframe_columns`
            # (riga 244). Drift fra le 2 implementazioni era P3 ma diventava
            # P1 al primo bugfix applicato solo a uno dei due rami. Post-fix:
            # delega all'helper canonico che gia' usa il pattern lazy-copy.
            df = _coerce_json_dataframe_columns(pd.DataFrame(data))

        elif estensione == ".parquet":
            # Pre-fix / Post-fix: vedi nota in `_load_dataframe_from_bytes`.
            # Terzo (e ultimo) call site di `pd.read_parquet` nel modulo:
            # tenuti allineati per garantire round-trip integer+null
            # lossless indipendentemente da come l'artifact entra
            # (bytes blob, path artifact, path filesystem diretto).
            df = pd.read_parquet(file_path, dtype_backend="numpy_nullable")

        elif estensione in [".geojson", ".gpkg", ".shp"]:
            import geopandas as gpd

            if layer and estensione == ".gpkg":
                df = gpd.read_file(file_path, layer=layer)
            else:
                df = gpd.read_file(file_path)

            if estensione == ".shp":
                logger.warning(
                    "Shapefile caricato - i nomi colonne potrebbero "
                    "essere troncati a 10 caratteri!"
                )

        elif estensione == ".gdb" or (file_path.is_dir() and file_path.suffix.lower() == ".gdb"):
            df = _carica_filegdb(file_path, layer)

        elif estensione == ".zip":
            df = _carica_da_zip(file_path, sheet=sheet, layer=layer)

        else:
            logger.warning(f"Formato {estensione} non riconosciuto, tentativo come CSV")
            df = _read_csv_safe(file_path)

        duration_ms = round((_time_mod.monotonic() - _t0) * 1000, 1)
        logger.info(
            f"[PERF] load_dataframe op=parse fmt={estensione or '<none>'} "
            f"file={file_path.name} file_size={file_size} "
            f"rows={len(df)} cols={len(df.columns)} duration_ms={duration_ms}"
        )
        logger.info(f"Caricato {file_path.name}: {len(df)} righe, {len(df.columns)} colonne", op="load")
        return df

    except Exception as e:
        logger.error(f"[CRASH] Errore caricamento {file_path.name}: {e}", exc_info=True)
        raise


def load_arcgis_feature_dataframe(file_path: Union[str, Path]) -> pd.DataFrame:
    """
    Load an ArcGIS feature write input preserving the legacy fail-fast fallback.

    Unknown extensions are attempted as GeoJSON/geospatial content first. They
    fall back to CSV only for CSV-like suffixes (.tsv, .txt), so corrupt binary
    payloads do not get silently parsed as text.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {file_path}")

    suffix = path.suffix.lower()

    try:
        import geopandas as gpd

        if suffix == ".geojson":
            return gpd.read_file(path)
        if suffix == ".gpkg":
            return gpd.read_file(path)
        if suffix in (".shp",):
            return gpd.read_file(path)
    except ImportError:
        pass

    if suffix == ".csv":
        df = _read_csv_safe(path)
        geom_col = "geometry" if "geometry" in df.columns else None

        if geom_col:
            try:
                import geopandas as gpd
                from app.core.dataframes.geometry_parsing import parse_geometry_series

                df["geometry"] = parse_geometry_series(df[geom_col])
                return gpd.GeoDataFrame(df, geometry="geometry")
            except (ImportError, Exception) as e:
                logger.warning(f"Errore conversione geometria WKT: {e}")
        return df

    if suffix == ".json":
        return pd.read_json(path)

    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)

    try:
        import geopandas as gpd

        return gpd.read_file(path)
    except Exception as exc:
        logger.warning(
            f"Lettura come GeoJSON fallita per {path.name} "
            f"(suffix={suffix!r}): {type(exc).__name__}: {exc}"
        )
        if suffix in (".tsv", ".txt"):
            logger.info(
                f"Fallback a CSV per {path.name}: suffix {suffix!r} plausibile"
            )
            return _read_csv_safe(path)
        raise ValueError(
            f"Formato file non supportato: {path.name} (suffix={suffix!r}). "
            f"Formati supportati: .csv, .tsv, .txt, .geojson, .gpkg, .shp, "
            f".json, .xlsx, .xls. Lettura GeoJSON fallita: {exc}"
        ) from exc


def _carica_filegdb(file_path: Path, layer: Optional[str] = None) -> pd.DataFrame:
    """Load a DataFrame from an Esri File Geodatabase."""
    import geopandas as gpd

    if layer:
        return gpd.read_file(file_path, layer=layer)

    import fiona

    layers = fiona.listlayers(file_path)
    if not layers:
        raise ValueError(f"Nessun layer trovato in {file_path}")

    return gpd.read_file(file_path, layer=layers[0])


def _carica_da_zip(
    zip_path: Path,
    sheet: Optional[str] = None,
    layer: Optional[str] = None,
) -> pd.DataFrame:
    """Load a DataFrame from a ZIP archive.

    Pre-fix: estraeva il contenuto ZIP su una temp dir via
    safe_extractall, quindi caricava il file estratto da disk.
    3 callsite identici per shapefile / FileGDB / singolo file.
    Post-fix: pyogrio 0.12+ accetta ZIP bytes nativi e gestisce
    internamente /vsizip. Lettura ZIP-from-bytes -> GeoDataFrame
    senza extractall e senza temp dir. La ZipFile e' aperta UNA
    volta sola via BytesIO, e i cap zip-bomb sono validati prima
    di qualunque `inner.read()`.
    """
    from app.core.io.geospatial import read_vector_bytes
    from app.core.io.zip_guards import validate_zip_bomb_limits

    zip_bytes = zip_path.read_bytes()

    with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
        # SAFETY: bomb guard PRIMA di qualunque inner.read().
        validate_zip_bomb_limits(zf)
        contenuto = zf.namelist()

        shp_files = [n for n in contenuto if n.lower().endswith(".shp")]
        if shp_files:
            df = read_vector_bytes(zip_bytes, layer=layer)
            logger.warning(
                "Shapefile caricato - i nomi colonne potrebbero "
                "essere troncati a 10 caratteri!"
            )
            return df

        gdb_entries = [
            n for n in contenuto if ".gdb/" in n or n.lower().endswith(".gdb")
        ]
        if gdb_entries:
            return read_vector_bytes(zip_bytes, layer=layer)

        if len(contenuto) == 1:
            # Singolo file (es. CSV/JSON/Parquet zippato): non possiamo
            # usare pyogrio (non sono vector). Leggi via zipfile in RAM
            # + dispatch su `_load_dataframe_from_bytes` per i formati
            # tabulari supportati.
            inner_name = contenuto[0]
            with zf.open(inner_name) as inner:
                inner_bytes = inner.read()
            inner_suffix = Path(inner_name).suffix.lower().lstrip(".")
            if inner_suffix in _IN_MEMORY_ARTIFACT_FORMATS:
                return _load_dataframe_from_bytes(
                    inner_bytes,
                    format_hint=inner_suffix,
                    sheet=sheet,
                    layer=layer,
                )
            raise ValueError(
                f"ZIP contiene file singolo con estensione non gestita: "
                f"{inner_name!r}"
            )

    anteprima = contenuto[:10]
    suffisso = "..." if len(contenuto) > 10 else ""
    raise ValueError(f"Formato ZIP non riconosciuto. Contenuto: {anteprima}{suffisso}")


def find_latest_output_file(output_dir: Union[str, Path]) -> Optional[Path]:
    """Find the newest file in an output directory."""
    output_dir = Path(output_dir)

    if not output_dir.exists():
        return None

    files = [f for f in output_dir.iterdir() if f.is_file()]
    if not files:
        return None

    return max(files, key=lambda x: x.stat().st_mtime)


def save_dataframe_to_bytes(
    df: pd.DataFrame,
    format: str = "csv",
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> bytes:
    """Serialize a DataFrame, dispatching explicit file I/O boundaries by format."""
    formato = format.lower()

    if formato in _GEOSPATIAL_FORMATS:
        from app.core.dataframes.geospatial_io import geospatial_dataframe_to_bytes

        return geospatial_dataframe_to_bytes(df, formato, geom_column, crs)

    return tabular_dataframe_to_bytes(df, formato)
