"""
Pure tabular DataFrame I/O helpers.

This module intentionally owns pandas-oriented loading, tabular saving,
column filtering, and JSON-safe row normalization. Geospatial export lives in
``app.core.files.geospatial_io`` so callers do not pull geometry decisions from
the legacy utils facade.
"""

import time as _time_mod

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from app.core.resources.exceptions import MemoryLimitExceededError
from app.utils.zip_safe import safe_extractall
from app.utils.logger import get_logger

logger = get_logger(__name__)

_GEOSPATIAL_FORMATS = {"geojson", "gpkg", "shp"}


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
            df = pd.DataFrame(data)

            for col in df.columns:
                if df[col].dtype == "object":
                    sample = df[col].dropna().head(100).astype(str)
                    has_underscore = sample.str.contains("_", na=False).any()
                    if not has_underscore:
                        converted = pd.to_numeric(df[col], errors="coerce")
                        original_count = df[col].notna().sum()
                        converted_count = converted.notna().sum()
                        if original_count > 0 and converted_count == original_count:
                            df[col] = converted

        elif estensione == ".parquet":
            df = pd.read_parquet(file_path)

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


def _compact_whole_number_floats(df: pd.DataFrame) -> pd.DataFrame:
    """Restituisce un DataFrame normalizzato per la serializzazione tabular.

    Due passi:

    1. Colonne ``object`` che contengono solo ``Decimal``/numeri vengono
       convertite a ``float`` (o ``Int64`` se interi). PostgreSQL ``AVG``,
       ``SUM`` su tipi numeric ritornano ``Decimal`` via psycopg2:
       pandas li tiene come ``object`` e il CSV writer non puo' usare
       ``float_format``, producendo output come ``36.5000000000000000``
       (16 decimali del repr di Decimal).
    2. Colonne ``float`` con tutti i non-NaN interi vengono convertite
       a ``Int64`` nullable. pandas promuove silenziosamente int -> float
       quando incontra NaN: senza questo passo, un id/age/conteggio
       esce ``34.0`` invece di ``34``.

    Trasformazione confinata al boundary di serializzazione (no mutazione
    upstream).
    """
    if df is None or df.empty:
        return df
    from decimal import Decimal

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
                            [int(v) if v is not None and not pd.isna(v) else pd.NA
                             for v in s],
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
        final_path = base_path.with_suffix(".parquet")
        df.to_parquet(final_path, index=False)
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
            from app.core.files.geospatial_io import save_geospatial_dataframe

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


def save_dataframe_to_bytes(
    df: pd.DataFrame,
    format: str = "csv",
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326",
) -> bytes:
    """Serialize a DataFrame, dispatching explicit file I/O boundaries by format."""
    formato = format.lower()

    if formato in _GEOSPATIAL_FORMATS:
        from app.core.files.geospatial_io import geospatial_dataframe_to_bytes

        return geospatial_dataframe_to_bytes(df, formato, geom_column, crs)

    return tabular_dataframe_to_bytes(df, formato)


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

