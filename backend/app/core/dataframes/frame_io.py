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
    dataframe_to_rest_records,
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
from app.utils.zip_safe import safe_extractall
from app.utils.logger import get_logger

logger = get_logger(__name__)

_GEOSPATIAL_FORMATS = {"geojson", "gpkg", "shp"}
_IN_MEMORY_ARTIFACT_FORMATS = {"csv", "json", "xlsx", "xls", "parquet", "geojson"}
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
    "dataframe_to_rest_records",
    "drop_rows_reset_index",
    "empty_dataframe",
    "empty_object_series",
    "false_boolean_series",
    "save_dataframe",
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
    "save_tabular_dataframe",
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

    def save_dataframe(
        self,
        df: pd.DataFrame,
        output_path: Union[str, Path],
        format: str = "csv",
        geom_column: Optional[str] = None,
        crs: str = "EPSG:4326",
    ) -> Path:
        return save_dataframe(df, output_path, format, geom_column, crs)

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
        out = df.copy()
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
        suffix = ".gpkg" if format_hint == "gpkg" else f".{format_hint}"
        with tempfile.NamedTemporaryFile(delete=False, prefix="plenora-artifact-", suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return load_dataframe_from_file(tmp_path, sheet=sheet, layer=layer)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
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
        expected_suffix = ".gpkg" if fmt == "gpkg" else f".{fmt}"
        if fmt != "gdb" and artifact_path.suffix.lower() != expected_suffix:
            # Pre-fix: `with NamedTemporaryFile(...) as tmp: tmp_path = ...`
            # creava una finestra in cui il file su disco poteva rimanere
            # orfano se l'uscita dal `with` falliva tra creazione e
            # assegnazione di `tmp_path`. Post-fix: creazione esplicita
            # via NamedTemporaryFile + close immediato, tmp_path catturato
            # prima del try block — il finally esegue unlink in qualsiasi
            # caso di errore successivo (shutil.copyfile o load_*).
            tmp_obj = tempfile.NamedTemporaryFile(
                delete=False,
                prefix="plenora-artifact-",
                suffix=expected_suffix,
            )
            tmp_path = Path(tmp_obj.name)
            tmp_obj.close()
            try:
                shutil.copyfile(artifact_path, tmp_path)
                return load_dataframe_from_file(tmp_path, sheet=sheet, layer=layer)
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        return load_dataframe_from_file(artifact_path, sheet=sheet, layer=layer)

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
    """Load a DataFrame from a ZIP archive."""
    import geopandas as gpd

    with zipfile.ZipFile(zip_path, "r") as zf:
        contenuto = zf.namelist()

        shp_files = [n for n in contenuto if n.lower().endswith(".shp")]
        if shp_files:
            with tempfile.TemporaryDirectory() as tmpdir:
                safe_extractall(zf, tmpdir)
                shp_path = Path(tmpdir) / shp_files[0]
                df = gpd.read_file(shp_path)
                logger.warning(
                    "Shapefile caricato - i nomi colonne potrebbero "
                    "essere troncati a 10 caratteri!"
                )
            return df

        gdb_entries = [n for n in contenuto if ".gdb/" in n or n.lower().endswith(".gdb")]
        if gdb_entries:
            with tempfile.TemporaryDirectory() as tmpdir:
                safe_extractall(zf, tmpdir)
                gdb_dirs = list(Path(tmpdir).rglob("*.gdb"))
                if not gdb_dirs:
                    raise ValueError("FileGDB non trovato nello ZIP")
                return _carica_filegdb(gdb_dirs[0], layer)

        if len(contenuto) == 1:
            with tempfile.TemporaryDirectory() as tmpdir:
                safe_extractall(zf, tmpdir)
                file_estratto = Path(tmpdir) / contenuto[0]
                return load_dataframe_from_file(file_estratto, sheet=sheet, layer=layer)

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


def save_tabular_dataframe(df: pd.DataFrame, base_path: Path, formato: str) -> Path:
    """Save a DataFrame in a non-geospatial format."""
    df = _compact_whole_number_floats(df)
    if formato == "csv":
        final_path = base_path.with_suffix(".csv")
        # `float_format='%.15g'`: scrive `52000` invece di `52000.0` ma
        # preserva `45000.5` con i decimali reali. Resta dentro 15 cifre
        # significative (precisione float64) e non incappa nella
        # notazione scientifica del semplice `%g` su numeri molto piccoli.
        df.to_csv(final_path, index=False, encoding="utf-8", float_format="%.15g")
        return final_path

    if formato == "xlsx":
        final_path = base_path.with_suffix(".xlsx")
        try:
            df.to_excel(final_path, index=False)
        except Exception as e:
            logger.info("Excel standard fallito, riprovo con ZIP64: %s", e)
            with pd.ExcelWriter(
                final_path,
                engine="xlsxwriter",
                engine_kwargs={"options": {"use_zip64": True}},
            ) as writer:
                df.to_excel(writer, index=False)
        return final_path

    if formato == "json":
        final_path = base_path.with_suffix(".json")
        records = sanitize_for_json(df.to_dict(orient="records"))
        with open(final_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str, ensure_ascii=False)
        return final_path

    if formato == "parquet":
        # Pre-fix: scrittura diretta su `final_path` (non-atomic) +
        # `to_parquet` senza `engine="pyarrow"` esplicito. Due bug
        # combinati:
        #   1. Engine implicito: su install con fastparquet l'Int64
        #      nullable degrada a float64 silenziosamente.
        #   2. Non-atomic write: un reader concorrente puo' vedere il
        #      file parquet in stato parziale durante la scrittura.
        # Post-fix: write su `.tmp` + rename atomico + engine esplicito.
        final_path = base_path.with_suffix(".parquet")
        tmp_path = final_path.with_suffix(".parquet.tmp")
        try:
            df.to_parquet(tmp_path, index=False, engine="pyarrow")
            tmp_path.replace(final_path)
        except Exception:
            # Cleanup best-effort del .tmp in caso di errore write/rename
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise
        return final_path

    raise ValueError(f"Formato '{formato}' non supportato")


def save_dataframe(
    df: pd.DataFrame,
    output_path: Union[str, Path],
    format: str = "csv",
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> Path:
    """Save a DataFrame, dispatching explicit file I/O boundaries by format."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    base_path = output_path.with_suffix("")
    formato = format.lower()

    try:
        if formato in _GEOSPATIAL_FORMATS:
            from app.core.dataframes.geospatial_io import save_geospatial_dataframe

            final_path = save_geospatial_dataframe(df, base_path, formato, geom_column, crs)
        else:
            final_path = save_tabular_dataframe(df, base_path, formato)

        logger.info(f"Salvato: {final_path} ({len(df)} righe)", op="save")
        return final_path

    except ValueError as e:
        logger.warning(f"Salvataggio non riuscito {output_path}: {e}")
        raise
    except Exception as e:
        logger.error(f"[CRASH] Errore salvataggio {output_path}: {e}", exc_info=True)
        raise


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
