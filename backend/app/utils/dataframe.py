import re
"""
Utility centralizzate per operazioni su DataFrame in PLENORA-ETL.

Architettura
============
Questo modulo centralizza le operazioni su DataFrame usate da tutti i moduli,
eliminando la duplicazione di codice per caricamento, salvataggio e conversione.

Fornisce funzioni comuni per il caricamento, il salvataggio, il filtraggio
e la serializzazione di DataFrame pandas e GeoDataFrame.

Funzionalità principali:
    - load_dataframe_from_file(): caricamento multi-formato (CSV, XLSX, JSON, GeoJSON, SHP, GDB, ...)
    - save_dataframe(): salvataggio multi-formato con supporto geospaziale
    - filter_visible_columns(): filtraggio colonne in base alla configurazione canvas
    - sanitize_for_json(): conversione sicura per serializzazione JSON
    - infer_column_types(): inferenza automatica dei tipi colonna

Modulo creato per centralizzare la logica DataFrame precedentemente
duplicata in orchestrator_service, api_connection_service, query_executor
e file_source_service.

Uso:
    from app.utils.dataframe import (
        load_dataframe_from_file,
        save_dataframe,
        filter_visible_columns,
        sanitize_for_json
    )
"""

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

import numpy as np
import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# CARICAMENTO DATAFRAME
# =============================================================================

def load_dataframe_from_file(
    file_path: Union[str, Path],
    sheet: Optional[str] = None,
    layer: Optional[str] = None
) -> pd.DataFrame:
    """
    Carica un DataFrame da file in vari formati.

    Formati supportati:
        - CSV (.csv)
        - Excel (.xlsx, .xls) — con selezione foglio opzionale
        - JSON (.json) — formato records
        - Parquet (.parquet)
        - GeoJSON (.geojson) — via GeoPandas
        - GeoPackage (.gpkg) — con selezione layer opzionale
        - Shapefile (.shp) — attenzione: nomi colonne troncati a 10 caratteri
        - FileGDB (.gdb) — con selezione layer opzionale
        - ZIP (.zip) — contenente Shapefile, FileGDB o singolo file

    Args:
        file_path: Percorso del file da caricare.
        sheet: Nome del foglio per file Excel (opzionale).
        layer: Nome del layer per file GDB/GPKG (opzionale).

    Returns:
        DataFrame (o GeoDataFrame per formati geospaziali).

    Raises:
        FileNotFoundError: Se il file non esiste.
        ValueError: Se il formato non è supportato o il file è vuoto.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File non trovato: {file_path}")

    estensione = file_path.suffix.lower()

    # RB.#19 Step A: timing log per quantificare il parse pandas/geopandas.
    # Misurato indipendentemente da S3 download / temp blob write per
    # poter isolare il bottleneck reale (parser CSV vs I/O temp file vs
    # download remoto).
    import time as _time_mod
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
            # Leggi JSON con json.load per preservare stringhe con underscore
            # (pd.read_json converte "119_00074" in 11900074.0 perché _ è separatore migliaia)
            import json
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
            df = pd.DataFrame(data)
            
            # Applica inferenza tipi selettiva: converti a numerico solo colonne "pure"
            for col in df.columns:
                if df[col].dtype == 'object':
                    # Controlla se i valori contengono underscore (probabile codice/ID)
                    sample = df[col].dropna().head(100).astype(str)
                    has_underscore = sample.str.contains('_', na=False).any()
                    
                    if not has_underscore:
                        # Prova conversione a numerico
                        converted = pd.to_numeric(df[col], errors='coerce')
                        original_count = df[col].notna().sum()
                        converted_count = converted.notna().sum()
                        # Accetta solo se TUTTI i non-null sono convertiti (no data loss)
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
                    "⚠️ Shapefile caricato — i nomi colonne potrebbero "
                    "essere troncati a 10 caratteri!"
                )

        elif estensione == ".gdb" or (file_path.is_dir() and file_path.suffix.lower() == ".gdb"):
            df = _carica_filegdb(file_path, layer)

        elif estensione == ".zip":
            df = _carica_da_zip(file_path, sheet=sheet, layer=layer)

        else:
            # Fallback: prova come CSV
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
        # Logga solo il filename, NON il path completo (info disclosure: il
        # path completo può rivelare struttura del filesystem server).
        logger.error(f"[CRASH] Errore caricamento {file_path.name}: {e}", exc_info=True)
        raise


def _read_csv_safe(file_path: Path) -> pd.DataFrame:
    """Carica un CSV con detection robusta dell'encoding.

    Tenta utf-8 → utf-8-sig (BOM) → cp1252 (Windows) → latin-1 (mai fallisce).
    Senza questo, file in encoding non-UTF8 producono mojibake silenzioso o
    UnicodeDecodeError opaco. Bug ETL classico.
    """
    encodings_to_try = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
    last_err = None
    for enc in encodings_to_try:
        try:
            return pd.read_csv(file_path, low_memory=False, encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception:
            # Errori non-encoding: rilancia subito (es. file vuoto)
            raise
    # Tutti gli encoding hanno fallito (improbabile dato che latin-1 accetta qualsiasi byte)
    raise UnicodeDecodeError(
        last_err.encoding, last_err.object, last_err.start, last_err.end,
        f"Impossibile decodificare {file_path.name} con nessuno di {encodings_to_try}"
    )


def _carica_filegdb(file_path: Path, layer: Optional[str] = None) -> pd.DataFrame:
    """
    Carica un DataFrame da un FileGDB (Esri File Geodatabase).

    Se il layer non è specificato, usa il primo layer disponibile.

    Args:
        file_path: Percorso della directory .gdb.
        layer: Nome del layer da caricare (opzionale).

    Returns:
        GeoDataFrame con i dati del layer.

    Raises:
        ValueError: Se il FileGDB non contiene layer.
    """
    import geopandas as gpd

    if layer:
        return gpd.read_file(file_path, layer=layer)

    # Senza layer specificato, carica il primo disponibile
    import fiona
    layers = fiona.listlayers(file_path)
    if not layers:
        raise ValueError(f"Nessun layer trovato in {file_path}")

    return gpd.read_file(file_path, layer=layers[0])


def _carica_da_zip(
    zip_path: Path,
    sheet: Optional[str] = None,
    layer: Optional[str] = None
) -> pd.DataFrame:
    """
    Carica un DataFrame da un file ZIP.

    Gestisce tre scenari:
        1. Shapefile zippato (.shp + file accessori)
        2. FileGDB zippato (directory .gdb)
        3. Singolo file zippato (CSV, JSON, etc.)

    Args:
        zip_path: Percorso del file ZIP.
        sheet: Nome foglio per file Excel all'interno dello ZIP.
        layer: Nome layer per GDB/GPKG all'interno dello ZIP.

    Returns:
        DataFrame caricato dal contenuto dello ZIP.

    Raises:
        ValueError: Se il formato ZIP non è riconosciuto.
    """
    import geopandas as gpd
    from app.utils.zip_safe import safe_extractall

    with zipfile.ZipFile(zip_path, "r") as zf:
        contenuto = zf.namelist()

        # Caso 1: Shapefile zippato
        shp_files = [n for n in contenuto if n.lower().endswith(".shp")]
        if shp_files:
            with tempfile.TemporaryDirectory() as tmpdir:
                safe_extractall(zf, tmpdir)
                shp_path = Path(tmpdir) / shp_files[0]
                df = gpd.read_file(shp_path)
                logger.warning(
                    "⚠️ Shapefile caricato — i nomi colonne potrebbero "
                    "essere troncati a 10 caratteri!"
                )
            return df

        # Caso 2: FileGDB zippato
        gdb_entries = [n for n in contenuto if ".gdb/" in n or n.lower().endswith(".gdb")]
        if gdb_entries:
            with tempfile.TemporaryDirectory() as tmpdir:
                safe_extractall(zf, tmpdir)
                gdb_dirs = list(Path(tmpdir).rglob("*.gdb"))
                if not gdb_dirs:
                    raise ValueError("FileGDB non trovato nello ZIP")
                return _carica_filegdb(gdb_dirs[0], layer)

        # Caso 3: Singolo file zippato — caricamento ricorsivo
        if len(contenuto) == 1:
            with tempfile.TemporaryDirectory() as tmpdir:
                safe_extractall(zf, tmpdir)
                file_estratto = Path(tmpdir) / contenuto[0]
                return load_dataframe_from_file(file_estratto, sheet=sheet, layer=layer)

        # Formato ZIP non riconosciuto
        anteprima = contenuto[:10]
        suffisso = "..." if len(contenuto) > 10 else ""
        raise ValueError(f"Formato ZIP non riconosciuto. Contenuto: {anteprima}{suffisso}")


def find_latest_output_file(output_dir: Union[str, Path]) -> Optional[Path]:
    """
    Trova il file più recente in una directory di output.

    Usa la data di modifica (st_mtime) per determinare il file più recente.

    Args:
        output_dir: Directory in cui cercare.

    Returns:
        Path del file più recente, o None se la directory è vuota o non esiste.
    """
    output_dir = Path(output_dir)

    if not output_dir.exists():
        return None

    files = [f for f in output_dir.iterdir() if f.is_file()]

    if not files:
        return None

    return max(files, key=lambda x: x.stat().st_mtime)


# =============================================================================
# SALVATAGGIO DATAFRAME
# =============================================================================

def save_dataframe(
    df: pd.DataFrame,
    output_path: Union[str, Path],
    format: str = "csv",
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326"
) -> Path:
    """
    Salva un DataFrame in vari formati.

    Per formati geospaziali (geojson, gpkg, shp), converte automaticamente
    il DataFrame in GeoDataFrame se necessario. Per Shapefile, il risultato
    è un file ZIP contenente tutti i file dello shapefile (.shp, .shx, .dbf, .prj, .cpg).

    Args:
        df: DataFrame da salvare.
        output_path: Percorso di output (l'estensione verrà gestita automaticamente).
        format: Formato di output (csv, xlsx, json, parquet, geojson, gpkg, shp).
        geom_column: Colonna geometria per formati geo (opzionale, auto-detect).
        crs: Sistema di riferimento per output geospaziali (default: WGS84).

    Returns:
        Path del file salvato (ZIP per shapefile).

    Raises:
        ValueError: Se il formato non è supportato o manca la geometria per formati geo.
    """
    from app.utils.geo import to_geodataframe, detect_geometry_column

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Rimuovi estensione per gestirla in base al formato
    base_path = output_path.with_suffix("")
    formato = format.lower()
    formati_geo = ["geojson", "gpkg", "shp"]


    try:
        # --- Formati tabulari ---

        if formato == "csv":
            final_path = base_path.with_suffix(".csv")
            # UTF-8 esplicito: pandas ha utf-8 come default ma il contratto
            # deve essere leggibile. Il reader in _read_csv_safe prova prima
            # utf-8 -> coerenza write/read a prova di locale di sistema.
            df.to_csv(final_path, index=False, encoding="utf-8")

        elif formato == "xlsx":
            final_path = base_path.with_suffix(".xlsx")
            try:
                df.to_excel(final_path, index=False)
            except Exception as e:
                # File troppo grande per ZIP standard — riprova con ZIP64
                logger.info("Excel standard fallito, riprovo con ZIP64: %s", e)
                import pandas as _pd
                with _pd.ExcelWriter(
                    final_path, engine="xlsxwriter",
                    engine_kwargs={"options": {"use_zip64": True}}
                ) as writer:
                    df.to_excel(writer, index=False)

        elif formato == "json":
            final_path = base_path.with_suffix(".json")
            records = sanitize_for_json(df.to_dict(orient="records"))
            with open(final_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, default=str, ensure_ascii=False)

        elif formato == "parquet":
            final_path = base_path.with_suffix(".parquet")
            df.to_parquet(final_path, index=False)

        # --- Formati geospaziali ---

        elif formato in formati_geo:
            final_path = _salva_formato_geo(
                df, base_path, formato, geom_column, crs,
                to_geodataframe, detect_geometry_column
            )

        else:
            raise ValueError(f"Formato '{formato}' non supportato")

        logger.info(f"Salvato: {final_path} ({len(df)} righe)", op="save")
        return final_path

    except ValueError as e:
        # ValueError = errore logico atteso (es. nessuna geometria, formato non supportato)
        # → WARNING, non ERROR: è una condizione gestita da _salva_risultato
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
    """Serializza un DataFrame in bytes in-memory (nessun disk I/O).

    Formati supportati in-memory: csv, xlsx, json, parquet, geojson.
    Formati NON supportati (richiedono filesystem per tool binari):
      - gpkg  (SQLite-based, il driver OGR vuole un path)
      - shp   (multi-file .shp/.shx/.dbf/.prj zippati)
    Per questi formati usa la versione path-based `save_dataframe`.

    Complementare a `save_dataframe`: stesso contract output (bytes che
    il reader corrispondente sa decodificare 1:1), ma senza passaggio
    per DATA_DIR/temp. Usato dai 4 callsite che poi fanno save_result_blob
    o eseguibili.storage.save (uno-step in-memory → destinazione finale).

    Args:
        df: DataFrame (o GeoDataFrame) da serializzare
        format: 'csv'|'xlsx'|'json'|'parquet'|'geojson'
        geom_column: colonna geometria per geojson (auto-detect se None)
        crs: CRS per output geospaziali (default WGS84)

    Returns:
        bytes — content pronto per upload DB blob / S3 / altro sink.

    Raises:
        ValueError: formato non supportato o filesystem-only (gpkg, shp)
    """
    from app.utils.geo import to_geodataframe, detect_geometry_column

    formato = format.lower()

    if formato == "csv":
        buf = io.StringIO()
        # encoding='utf-8' esplicito per coerenza col contract checker
        # (AST test cerca il kwarg su qualsiasi to_csv con arg positional).
        # Nel caso StringIO pandas ignora l'encoding effettivo (il buf e'
        # text-mode), noi lo applichiamo a getvalue().encode() sotto.
        df.to_csv(buf, index=False, encoding="utf-8")
        return buf.getvalue().encode("utf-8")

    if formato == "xlsx":
        buf = io.BytesIO()
        # Engine di default (openpyxl) supporta BytesIO. Per file >MAX zip
        # standard (rarissimo in pipeline tabulari) il path-based ha il
        # fallback xlsxwriter+zip64; qui se emerge errore lo propaghiamo.
        df.to_excel(buf, index=False)
        return buf.getvalue()

    if formato == "json":
        records = sanitize_for_json(df.to_dict(orient="records"))
        return json.dumps(records, indent=2, default=str, ensure_ascii=False).encode("utf-8")

    if formato == "parquet":
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue()

    if formato == "geojson":
        # Converti a GeoDataFrame se necessario (stesso pattern del
        # path-based `_salva_formato_geo`).
        if geom_column is None:
            geom_column = detect_geometry_column(df)
        try:
            import geopandas as gpd
        except ImportError:
            raise ValueError("geopandas non installato: formato geojson non disponibile")
        if not isinstance(df, gpd.GeoDataFrame):
            if not geom_column:
                raise ValueError("geojson: serve una colonna geometria")
            gdf = to_geodataframe(df, geom_column, crs)
        else:
            gdf = df
            if geom_column and gdf.geometry.name != geom_column:
                gdf = gdf.set_geometry(geom_column)
        # GeoDataFrame.to_json() ritorna stringa GeoJSON ottimizzata.
        return gdf.to_json().encode("utf-8")

    if formato in ("gpkg", "shp"):
        raise ValueError(
            f"Formato '{formato}' richiede filesystem (tool binari / multi-file). "
            f"Usa save_dataframe(path-based) invece di save_dataframe_to_bytes."
        )

    raise ValueError(f"Formato '{formato}' non supportato")


def _sanitize_layer_name(name: str) -> str:
    """
    Rimuove caratteri non validi dal nome layer GPKG/OGR.
    Ammessi: lettere, cifre, underscore. Tutto il resto → underscore.
    """
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    # Non può iniziare con una cifra
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "layer"


def _salva_formato_geo(
    df: pd.DataFrame,
    base_path: Path,
    formato: str,
    geom_column: Optional[str],
    crs: str,
    to_geodataframe,
    detect_geometry_column
) -> Path:
    """
    Salva un DataFrame in formato geospaziale (GeoJSON, GPKG o Shapefile).

    Gestisce:
        - DataFrame vuoti (crea file geo validi ma senza feature)
        - GeoDataFrame già pronti (con switch opzionale della colonna geometria)
        - DataFrame normali (conversione automatica a GeoDataFrame)
        - Colonne geometria multiple (converte le non attive in WKT)

    Args:
        df: DataFrame da salvare.
        base_path: Percorso base senza estensione.
        formato: Formato geo (geojson, gpkg, shp).
        geom_column: Colonna geometria esplicita (opzionale).
        crs: CRS di default per la conversione.
        to_geodataframe: Funzione di conversione (iniettata per evitare import circolari).
        detect_geometry_column: Funzione di rilevamento (iniettata).

    Returns:
        Path del file salvato.
    """
    import geopandas as gpd
    from shapely import wkt
    from shapely.geometry.base import BaseGeometry

    # --- Gestione DataFrame vuoto ---
    if len(df) == 0:
        logger.warning(f"DataFrame vuoto, creazione {formato} vuoto")
        return _salva_geo_vuoto(df, base_path, formato)

    # --- Preparazione GeoDataFrame ---
    if isinstance(df, gpd.GeoDataFrame):
        gdf = df.copy()

        # Se specificata una colonna geometria diversa da quella attiva, effettua lo switch
        if (geom_column
                and geom_column != gdf.geometry.name
                and geom_column in gdf.columns):
            gdf = _switch_colonna_geometria(gdf, geom_column)

        # Converti le colonne geometria secondarie in WKT per l'export
        # (GeoPandas non supporta colonne geometria multiple in un singolo file)
        gdf = _converti_geometrie_secondarie_in_wkt(gdf)

        if gdf.crs is None:
            gdf = gdf.set_crs(crs)

    else:
        # Conversione automatica DataFrame → GeoDataFrame
        if geom_column is None:
            geom_column = detect_geometry_column(df)

        if geom_column is None:
            # Fallback: se ci sono colonne lat/lon, costruisce geometria Point automaticamente
            lat_col = next((c for c in df.columns if c.lower() in ("lat", "latitude", "lat_wgs84")), None)
            lon_col = next((c for c in df.columns if c.lower() in ("lon", "lng", "longitude", "long", "lon_wgs84")), None)
            if lat_col and lon_col:
                import geopandas as gpd
                from shapely.geometry import Point
                df = df.copy()
                # Vectorized: niente df.apply row-by-row
                lons = pd.to_numeric(df[lon_col], errors='coerce')
                lats = pd.to_numeric(df[lat_col], errors='coerce')
                valid = lons.notna() & lats.notna()
                df["geometry"] = [
                    Point(lon, lat) if v else None
                    for lon, lat, v in zip(lons, lats, valid)
                ]
                geom_column = "geometry"
                logger.info(
                    f"Auto-creata colonna geometry da lat='{lat_col}' lon='{lon_col}' "
                    f"per salvataggio {formato}"
                )
            else:
                raise ValueError(
                    f"Nessuna colonna geometria trovata per formato {formato}. "
                    f"Colonne disponibili: {list(df.columns)}. "
                    f"Usa CSV o JSON per dati non spaziali."
                )

        gdf = to_geodataframe(df, geom_column, crs=crs)

    # --- Salvataggio nel formato richiesto ---
    if formato == "geojson":
        final_path = base_path.with_suffix(".geojson")
        gdf.to_file(final_path, driver="GeoJSON")

    elif formato == "gpkg":
        final_path = base_path.with_suffix(".gpkg")
        layer_name = _sanitize_layer_name(base_path.stem)
        gdf.to_file(final_path, driver="GPKG", layer=layer_name)

    elif formato == "shp":
        final_path = _salva_shapefile_zippato(gdf, base_path, df.columns)

    return final_path


def _switch_colonna_geometria(gdf, geom_column: str):
    """
    Imposta una colonna diversa come geometria attiva nel GeoDataFrame.

    Se la colonna contiene stringhe WKT, le converte in oggetti Shapely.

    Args:
        gdf: GeoDataFrame da modificare.
        geom_column: Nome della nuova colonna geometria attiva.

    Returns:
        GeoDataFrame con la geometria attiva cambiata.
    """
    from shapely import wkt
    from shapely.geometry.base import BaseGeometry


    col_data = gdf[geom_column]
    if col_data.dtype == "object":
        _non_null = col_data.dropna()
        primo_valido = _non_null.iloc[0] if not _non_null.empty else None
        if primo_valido and isinstance(primo_valido, str):
            # Converte WKT in oggetti geometria
            gdf[geom_column] = col_data.apply(
                lambda x: wkt.loads(x) if pd.notna(x) and isinstance(x, str) else x
            )

    return gdf.set_geometry(geom_column)


def _converti_geometrie_secondarie_in_wkt(gdf):
    """
    Converte tutte le colonne geometria non attive in stringhe WKT.

    Necessario perché GeoPandas non supporta l'export di file con
    colonne geometria multiple.

    Args:
        gdf: GeoDataFrame da processare.

    Returns:
        GeoDataFrame con le geometrie secondarie convertite in WKT.
    """
    from shapely.geometry.base import BaseGeometry

    nome_geometria_attiva = gdf.geometry.name

    for col in gdf.columns:
        if col == nome_geometria_attiva:
            continue

        col_data = gdf[col].dropna()
        if col_data.empty:
            continue

        primo_valido = col_data.iloc[0]
        if isinstance(primo_valido, BaseGeometry):
            gdf[col] = col_data.apply(
                lambda x: x.wkt if isinstance(x, BaseGeometry) else x
            )

    return gdf


def _salva_geo_vuoto(df: pd.DataFrame, base_path: Path, formato: str) -> Path:
    """
    Crea un file geospaziale vuoto ma strutturalmente valido.

    Args:
        df: DataFrame vuoto (usato per i nomi colonne).
        base_path: Percorso base senza estensione.
        formato: Formato geo (geojson, gpkg, shp).

    Returns:
        Path del file creato.
    """
    import geopandas as gpd

    if formato == "geojson":
        final_path = base_path.with_suffix(".geojson")
        geojson_vuoto = {"type": "FeatureCollection", "features": []}
        with open(final_path, "w", encoding="utf-8") as f:
            json.dump(geojson_vuoto, f)
        return final_path

    # Per GPKG e SHP, crea un GeoDataFrame vuoto con le stesse colonne
    gdf_vuoto = gpd.GeoDataFrame(
        {col: [] for col in df.columns},
        geometry=[],
        crs="EPSG:4326"
    )

    if formato == "gpkg":
        final_path = base_path.with_suffix(".gpkg")
        layer_name = _sanitize_layer_name(base_path.stem)
        gdf_vuoto.to_file(final_path, driver="GPKG", layer=layer_name)
    elif formato == "shp":
        final_path = _salva_shapefile_zippato(gdf_vuoto, base_path, df.columns)

    return final_path


def _salva_shapefile_zippato(
    gdf,
    base_path: Path,
    colonne_originali
) -> Path:
    """
    Salva un GeoDataFrame come Shapefile zippato (.zip).

    Lo Shapefile viene creato in una directory temporanea, poi tutti i file
    generati (.shp, .shx, .dbf, .prj, .cpg, etc.) vengono compressi in un ZIP.

    Emette un warning se ci sono colonne con nomi > 10 caratteri (limite Shapefile).

    Args:
        gdf: GeoDataFrame da salvare.
        base_path: Percorso base per il file ZIP (senza estensione).
        colonne_originali: Nomi colonne originali (per warning troncamento).

    Returns:
        Path del file ZIP creato.
    """
    nome_shp = base_path.stem

    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = Path(tmpdir) / f"{nome_shp}.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile")

        # Crea ZIP con tutti i file dello Shapefile
        zip_path = base_path.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmpdir).glob(f"{nome_shp}.*"):
                zf.write(f, f.name)

        # Warning per colonne con nomi lunghi (limite Shapefile: 10 caratteri)
        colonne_lunghe = [c for c in colonne_originali if len(str(c)) > 10]
        if colonne_lunghe:
            logger.warning(
                f"⚠️ Shapefile: {len(colonne_lunghe)} colonne con nomi > 10 caratteri "
                f"saranno troncate: {colonne_lunghe[:5]}..."
            )

    return zip_path


# =============================================================================
# FILTRAGGIO COLONNE
# =============================================================================

def filter_visible_columns(
    df: pd.DataFrame,
    node_columns: List[Dict[str, Any]]
) -> pd.DataFrame:
    """
    Filtra le colonne di un DataFrame mantenendo solo quelle visibili.

    Usa la configurazione del nodo canvas per determinare quali colonne
    mostrare. Le colonne con visible=True (o senza il campo visible)
    vengono mantenute.

    Args:
        df: DataFrame da filtrare.
        node_columns: Lista di dizionari con 'name' e 'visible' dalla configurazione nodo.
                      Es: [{'name': 'col1', 'visible': True}, {'name': 'col2', 'visible': False}]

    Returns:
        DataFrame con solo le colonne visibili (o l'originale se nessun filtro applicabile).
    """
    if not node_columns:
        return df

    # Estrai colonne visibili con eventuale alias
    visibili = [
        col for col in node_columns
        if col.get("visible", True)
    ]

    if not visibili:
        return df

    # Seleziona colonne disponibili nel DataFrame (per nome raw)
    # Gestisce duplicati: se lo stesso nome appare più volte, prendi solo la prima
    seen_names = set()
    colonne_da_selezionare = []  # (nome_raw, alias_finale)
    for col in visibili:
        raw_name = col.get("name", "")
        alias = col.get("alias") or raw_name
        if raw_name in df.columns and raw_name not in seen_names:
            colonne_da_selezionare.append((raw_name, alias))
            seen_names.add(raw_name)

    if not colonne_da_selezionare:
        logger.warning("Nessuna colonna visibile trovata nel DataFrame")
        return df

    # Seleziona e rinomina
    raw_names = [c[0] for c in colonne_da_selezionare]
    alias_map = {c[0]: c[1] for c in colonne_da_selezionare if c[0] != c[1]}

    result = df[raw_names]
    if alias_map:
        result = result.rename(columns=alias_map)
    return result


# =============================================================================
# SERIALIZZAZIONE JSON
# =============================================================================

def sanitize_for_json(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Rende una lista di dizionari sicura per la serializzazione JSON.

    Converte i tipi non standard di Python/NumPy/Pandas/Shapely:
        - NaN/None → None
        - numpy int/float → Python int/float
        - numpy bool → Python bool
        - numpy array → Python list
        - Timestamp/datetime64 → stringa ISO
        - bytes → stringa UTF-8 (o hex se non decodificabile)
        - Shapely geometry → stringa WKT

    La conversione è ricorsiva: gestisce dict e liste annidate.

    Args:
        rows: Lista di dizionari da sanitizzare.

    Returns:
        Lista di dizionari con tutti i valori JSON-compatibili.
    """
    # Import lazy di Shapely per evitare dipendenze circolari
    try:
        from shapely.geometry.base import BaseGeometry
        shapely_disponibile = True
    except ImportError:
        shapely_disponibile = False

    def _sanitizza_valore(value):
        """Sanitizza ricorsivamente un singolo valore."""

        # Array NumPy → lista Python ricorsiva
        if isinstance(value, np.ndarray):
            return [_sanitizza_valore(v) for v in value.tolist()]

        # Lista Python → ricorsione su ogni elemento
        if isinstance(value, list):
            return [_sanitizza_valore(v) for v in value]

        # Dizionario → ricorsione su ogni valore
        if isinstance(value, dict):
            return {k: _sanitizza_valore(v) for k, v in value.items()}

        # NaN e None
        try:
            if value is None or (isinstance(value, float) and np.isnan(value)):
                return None
        except (TypeError, ValueError):
            pass  # NaN check su tipo non numerico, prosegui normalmente

        # Tipi numerici NumPy → Python nativi
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)

        # Timestamp → stringa
        if isinstance(value, (pd.Timestamp, np.datetime64)):
            return str(value)

        # Bytes → stringa UTF-8 o hex
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.hex()

        # Geometrie Shapely → WKT
        if shapely_disponibile and isinstance(value, BaseGeometry):
            return value.wkt if value and not value.is_empty else None

        return value

    # Applica sanitizzazione in-place per efficienza
    for row in rows:
        for chiave in list(row.keys()):
            row[chiave] = _sanitizza_valore(row[chiave])

    return rows


# =============================================================================
# UTILITY COLONNE
# =============================================================================

def infer_column_types(df: pd.DataFrame) -> List[Dict[str, str]]:
    """
    Inferisce i tipi delle colonne di un DataFrame.

    Mappa i dtype pandas ai tipi PLENORA-ETL:
        int* → "int", float/numeric → "real", bool → "bool",
        datetime/timestamp → "date", geometry → "geometry",
        object/altro → "str"

    Args:
        df: DataFrame da analizzare.

    Returns:
        Lista di dizionari [{'name': 'col1', 'type': 'int'}, ...].
    """
    colonne = []

    for col in df.columns:
        dtype = str(df[col].dtype).lower()

        if "int" in dtype:
            tipo = "int"
        elif any(t in dtype for t in ("float", "numeric", "decimal", "double")):
            tipo = "real"
        elif "bool" in dtype:
            tipo = "bool"
        elif any(t in dtype for t in ("datetime", "timestamp")):
            tipo = "date"
        elif "date" in dtype:
            tipo = "date"
        elif "geometry" in dtype:
            tipo = "geometry"
        else:
            tipo = "str"

        colonne.append({"name": col, "type": tipo})

    return colonne
