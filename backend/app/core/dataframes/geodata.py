"""
Utility centralizzate per operazioni geospaziali in PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json
from typing import Optional, Any

import pandas as pd
import geopandas as gpd
from shapely import wkt, wkb
from shapely.geometry import shape

from app.utils.logger import get_logger

logger = get_logger(__name__)


# COSTANTI

# Nomi comunemente usati per le colonne geometriche nei database e file GIS
GEOMETRY_COLUMN_NAMES = [
    "geometry", "geom", "the_geom", "shape", "wkb_geometry", "wkt"
]

# Prefissi validi per il formato WKT (Well-Known Text
WKT_PREFIXES = (
    "POINT", "LINESTRING", "POLYGON",
    "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON",
    "GEOMETRYCOLLECTION"
)


# PARSING GEOMETRIE


def is_geodataframe(value: Any) -> bool:
    """Return True when value is a GeoDataFrame."""
    return isinstance(value, gpd.GeoDataFrame)


def geodataframe_from_geometry(
    df: Any,
    geometry: str,
    crs: str = "EPSG:4326",
) -> Any:
    """Build a GeoDataFrame using the current geodataframe engine."""
    return gpd.GeoDataFrame(df, geometry=geometry, crs=crs)


def geodataframe_from_rows(
    rows: list[dict[str, Any]],
    geometry: str,
    crs: Any = None,
) -> Any:
    """Build a GeoDataFrame from row dictionaries."""
    return gpd.GeoDataFrame(rows, geometry=geometry, crs=crs)


def geodataframe_from_geometry_series(
    geometry: Any,
    crs: Any = None,
) -> Any:
    """Build a GeoDataFrame from a geometry series and CRS."""
    return gpd.GeoDataFrame(geometry=geometry, crs=crs)


def geoseries_from_geometry_values(
    values: Any,
    crs: Any = None,
    index: Any = None,
) -> Any:
    """Build a GeoSeries from geometry values through the geodata boundary."""
    return gpd.GeoSeries(values, crs=crs, index=index)


def empty_geodataframe(columns: list[str]) -> Any:
    """Build an empty GeoDataFrame with explicit columns."""
    return gpd.GeoDataFrame(columns=columns)


def geodataframe_sjoin(
    left: Any,
    right: Any,
    how: str = "inner",
    predicate: str = "intersects",
) -> Any:
    """Run a GeoPandas spatial join through the dataframe boundary."""
    return gpd.sjoin(left, right, how=how, predicate=predicate)


def geodataframe_sjoin_nearest(
    left: Any,
    right: Any,
    **kwargs: Any,
) -> Any:
    """Run a GeoPandas nearest spatial join through the dataframe boundary."""
    return gpd.sjoin_nearest(left, right, **kwargs)


def geodataframe_clip(df: Any, mask: Any) -> Any:
    """Clip a GeoDataFrame through the dataframe boundary."""
    return gpd.clip(df, mask)


def geodataframe_overlay(
    left: Any,
    right: Any,
    how: str = "intersection",
    keep_geom_type: bool = True,
) -> Any:
    """Run a GeoPandas overlay through the dataframe boundary."""
    return gpd.overlay(left, right, how=how, keep_geom_type=keep_geom_type)


def geodataframe_drop_duplicates_reset_index(df: Any) -> Any:
    """Drop duplicate geospatial rows and reset positional index."""
    return df.drop_duplicates().reset_index(drop=True)


def geodataframe_group_size_named(df: Any, by: str, name: str) -> Any:
    """Return a named group-size series for a geospatial frame."""
    return df.groupby(by).size().rename(name)


def geodataframe_group_aggregate_named(
    df: Any,
    by: str,
    column: str,
    func: str,
    name: str,
) -> Any:
    """Return a named group aggregate series for a geospatial frame."""
    return df.groupby(by)[column].agg(func).rename(name)


def geoseries_first_value(values: Any) -> Any:
    """Return the first value from a GeoSeries-like object."""
    return values.iloc[0]


def active_geometry_value_at(df: Any, index: Any) -> Any:
    """Return the active geometry value at an index."""
    return df.at[index, df.geometry.name]


def set_active_geometry_value_at(df: Any, index: Any, value: Any) -> None:
    """Set the active geometry value at an index."""
    df.at[index, df.geometry.name] = value


def geodataframe_majority_geometry_type(df: Any) -> tuple[str | None, set[str], int | None]:
    """Return majority geometry type, unique geometry types, and EPSG code."""
    majority_type = None
    unique_types: set[str] = set()
    srid = None
    if is_geodataframe(df) and df.geometry is not None and len(df) > 0:
        geom_types = df.geometry.dropna().geom_type
        if len(geom_types) > 0:
            majority_type = geom_types.value_counts().idxmax()
            unique_types = set(geom_types)
        if df.crs:
            srid = df.crs.to_epsg()
    return majority_type, unique_types, srid


def geodataframe_total_bounds(df: Any) -> Any:
    """Return total bounds for non-null active geometries."""
    if is_geodataframe(df) and df.geometry is not None and len(df) > 0:
        return df.geometry.dropna().total_bounds
    return None


def parse_geometry(value: Any) -> Optional[Any]:
    """
    Converte un valore in un oggetto geometria Shapely.

    Tenta il parsing in ordine di probabilità:
        1. GeoJSON (stringa JSON con 'type')
        2. WKT (stringa che inizia con nome tipo geometria)
        3. WKB/EWKB hex (stringa esadecimale lunga)
        4. WKB bytes (oggetto bytes/memoryview)
        5. Tentativo forzato WKB hex (ultimo fallback)

    Args:
        value: Valore da parsare. Accetta: str, bytes, memoryview,
               oggetto Shapely, None, NaN.

    Returns:
        Oggetto geometria Shapely, o None se il parsing fallisce.
    """
    # Gestione valori nulli e vuoti
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if value == "" or value == "None":
        return None

    # Se è già un oggetto geometria Shapely, restituiscilo direttamente
    if hasattr(value, "geom_type"):
        return value

    value_str = str(value).strip()
    if not value_str:
        return None

    # 1. Tentativo GeoJSON (inizia con '{'
    if value_str.startswith("{"):
        try:
            geojson = json.loads(value_str)
            if isinstance(geojson, dict) and "type" in geojson:
                geojson_type = geojson["type"]
                # FeatureCollection → prendi la geometria della prima feature
                if geojson_type == "FeatureCollection":
                    features = geojson.get("features", [])
                    if features and features[0].get("geometry"):
                        return shape(features[0]["geometry"])
                    return None
                # Feature → usa la sua geometry
                elif geojson_type == "Feature":
                    geom = geojson.get("geometry")
                    return shape(geom) if geom else None
                # Geometry diretta (Point, Polygon, ecc
                else:
                    return shape(geojson)
        except (json.JSONDecodeError, ValueError, Exception) as e:
            logger.debug("parse_geometry: GeoJSON parsing fallito per %r: %s", value_str[:80], e)

    # 2. Tentativo WKT (inizia con nome tipo geometria
    value_upper = value_str.upper()
    if any(value_upper.startswith(prefisso) for prefisso in WKT_PREFIXES):
        try:
            return wkt.loads(value_str)
        except Exception as e:
            logger.debug("parse_geometry: WKT parsing fallito per %r: %s", value_str[:80], e)

    # 3. Tentativo WKB/EWKB hex (stringa esadecimale lunga che inizia con '0'
    if _sembra_wkb_hex(value_str):
        geom = _parsa_wkb_hex(value_str)
        if geom is not None:
            return geom

    # 4. Tentativo WKB bytes (oggetto bytes o memoryview
    if isinstance(value, (bytes, memoryview)):
        try:
            return wkb.loads(bytes(value))
        except Exception as e:
            logger.debug("parse_geometry: WKB bytes parsing fallito: %s", e)

    # 5. Ultimo tentativo: forza interpretazione come WKB hex
    try:
        return wkb.loads(value_str, hex=True)
    except Exception as e:
        logger.debug("parse_geometry: tutti i tentativi falliti per %r: %s", value_str[:80], e)

    return None


def _sembra_wkb_hex(value: str) -> bool:
    """
    Euristica per determinare se una stringa potrebbe essere WKB in formato hex.

    Requisiti: lunghezza >= 20, inizia con '0', primi 20 caratteri tutti esadecimali.
    """
    if len(value) < 20 or not value.startswith("0"):
        return False
    return all(c in "0123456789ABCDEFabcdef" for c in value[:20])


def _parsa_wkb_hex(value_str: str) -> Optional[Any]:
    """
    Tenta il parsing di una stringa hex come WKB o EWKB.

    L'EWKB (Extended WKB) include un flag SRID nel byte del tipo geometria.
    Se il WKB standard fallisce, tenta di rimuovere il flag SRID e riprovare.

    Formato EWKB:
        byte_order (1 byte) + tipo_con_flag_srid (4 byte) + srid (4 byte) + geometria
        Il flag SRID è 0x20000000 (bit 5 nel terzo byte del tipo).
    """
    # Tentativo 1: WKB/EWKB standard (Shapely gestisce EWKB nativamente
    try:
        return wkb.loads(value_str, hex=True)
    except Exception as e:
        logger.debug("_parsa_wkb_hex: WKB standard fallito: %s", e)

    # Tentativo 2: EWKB con rimozione manuale del flag SRID
    try:
        if len(value_str) > 18 and value_str[8:10] == "20":
            # Rimuovi flag SRID dal tipo e salta i 4 byte dell'SRID
            wkb_pulito = (
                value_str[:2] +
                value_str[2:10].replace("20", "00") +
                value_str[18:]
            )
            return wkb.loads(wkb_pulito, hex=True)
    except Exception as e:
        logger.debug("_parsa_wkb_hex: EWKB con rimozione SRID fallito: %s", e)

    return None


def extract_srid_from_ewkb(value_str: str) -> Optional[int]:
    """
    Estrae il codice SRID da una stringa EWKB hex.

    L'SRID è codificato in little-endian nei byte 10-18 della stringa hex,
    solo se il flag SRID (0x20) è presente nel byte del tipo geometria.

    Args:
        value_str: Stringa EWKB in formato esadecimale.

    Returns:
        SRID come intero (es. 4326), o None se non trovato o non valido.
    """
    if not _sembra_wkb_hex(value_str) or len(value_str) < 18:
        return None

    try:
        # Verifica presenza flag SRID (0x20 nel tipo geometria
        if value_str[8:10] == "20":
            srid_hex = value_str[10:18]
            srid_bytes = bytes.fromhex(srid_hex)
            srid = int.from_bytes(srid_bytes, byteorder="little")
            if srid > 0:
                return srid
    except (ValueError, IndexError) as e:
        logger.debug("extract_srid_from_ewkb: estrazione SRID fallita: %s", e)

    return None


# CONVERSIONE GEODATAFRAME

def to_geodataframe(
    df: pd.DataFrame,
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326"
) -> gpd.GeoDataFrame:
    """
    Converte un DataFrame pandas in GeoDataFrame.

    Procedura:
        1. Se già GeoDataFrame, restituisce direttamente
        2. Auto-detect della colonna geometria (se non specificata)
        3. Parsing delle geometrie nella colonna rilevata
        4. Tentativo di estrazione SRID dalla prima geometria EWKB
        5. Creazione del GeoDataFrame con CRS appropriato

    Args:
        df: DataFrame sorgente contenente almeno una colonna geometria.
        geom_column: Nome esplicito della colonna geometria.
                     Se None, viene rilevata automaticamente.
        crs: Sistema di riferimento di default (usato se non rilevato da EWKB).

    Returns:
        GeoDataFrame con colonna 'geometry' e CRS impostato.

    Raises:
        ValueError: Se nessuna colonna geometria trovata o nessuna geometria valida.
    """
    # Se è già un GeoDataFrame, restituiscilo senza modifiche
    if isinstance(df, gpd.GeoDataFrame):
        return df

    # Auto-detect della colonna geometria se non specificata
    if geom_column is None:
        geom_column = detect_geometry_column(df)

    if geom_column is None or geom_column not in df.columns:
        raise ValueError(
            f"Nessuna colonna geometria trovata. "
            f"Colonne disponibili: {list(df.columns)}"
        )


    # Parsing di tutte le geometrie nella colonna
    geometrie = df[geom_column].apply(parse_geometry)

    # Validazione: almeno una geometria deve essere parsata con successo
    # Se fallisce, tenta di ricostruire WKT spezzato da CSV senza quoting
    # pd.read_csv senza quoting spezza POLYGON ((lon lat, lon lat, ...)) su più
    # colonne quando le virgole interne vengono interpretate come separatori
    # Strategia: cerca colonne Unnamed adiacenti e le concatena alla colonna
    # geom_column per ricostruire il WKT completo
    conteggio_valide = geometrie.notna().sum()
    if conteggio_valide == 0:
        geom_col_idx = list(df.columns).index(geom_column)
        unnamed_after = [
            c for c in df.columns[geom_col_idx + 1:]
            if str(c).startswith("Unnamed")
        ]
        if unnamed_after:
            logger.warning(
                f"Colonna geometria '{geom_column}' sembra spezzata da CSV senza quoting "
                f"({len(unnamed_after)} colonne Unnamed adiacenti). "
                f"Tentativo ricostruzione WKT..."
            )
            ricostruita = df[geom_column].astype(str)
            for col in unnamed_after:
                ricostruita = ricostruita + "," + df[col].astype(str)
            geometrie_ricostruite = ricostruita.apply(parse_geometry)
            if geometrie_ricostruite.notna().sum() > 0:
                logger.info(
                    f"Ricostruzione WKT riuscita: "
                    f"{geometrie_ricostruite.notna().sum()} geometrie valide"
                )
                geometrie = geometrie_ricostruite
                # Rimuovi le colonne Unnamed dal DataFrame (erano frammenti WKT
                df = df.drop(columns=unnamed_after)
                conteggio_valide = geometrie.notna().sum()

    if conteggio_valide == 0:
        # Controlla se i valori sembrano WKT troncato per dare un messaggio chiaro
        primo = df[geom_column].dropna().astype(str).iloc[0] if not df[geom_column].dropna().empty else ""
        primo_upper = primo.upper()
        if any(primo_upper.startswith(p) for p in WKT_PREFIXES) and ")" not in primo:
            raise ValueError(
                f"Nessuna geometria valida nella colonna '{geom_column}': "
                f"i valori sembrano WKT troncato (CSV senza quoting delle virgole). "
                f"Racchiudere i valori WKT tra doppi apici nel CSV, "
                f"es: \"POLYGON ((lon lat, lon lat, ...))\""
            )
        raise ValueError(f"Nessuna geometria valida nella colonna '{geom_column}'")


    # Tentativo di rilevare il CRS dalla prima geometria EWKB
    crs_rilevato = crs
    primo_valore = df[geom_column].dropna().iloc[0] if not df[geom_column].dropna().empty else None
    if primo_valore:
        srid = extract_srid_from_ewkb(str(primo_valore))
        if srid:
            crs_rilevato = f"EPSG:{srid}"

    # Preserva il nome originale della colonna geometria. Costruire
    # `gpd.GeoDataFrame(..., geometry=<GeoSeries>)` senza nome esplicito
    # avrebbe rinominato l'active geometry in "geometry": il runtime DF
    # avrebbe poi divergeva dai metadati canvas (che mantengono il nome
    # originale, es. "geom"), facendo fallire il salvataggio GeoJSON con
    # `set_geometry("geom")` -> "Unknown column geom".
    df_copia = df.copy()
    df_copia[geom_column] = geometrie.values
    gdf = gpd.GeoDataFrame(df_copia, geometry=geom_column, crs=crs_rilevato)

    logger.info(
        f"Convertito in GeoDataFrame: {len(gdf)} righe, CRS: {gdf.crs}, "
        f"geom_column='{geom_column}'"
    )
    return gdf


def detect_geometry_column(df: pd.DataFrame) -> Optional[str]:
    """
    Rileva automaticamente la colonna geometria in un DataFrame.

    Strategia di ricerca (in ordine di priorità):
        1. Nomi esatti comuni: geometry, geom, the_geom, shape, wkt (case-insensitive)
        2. Colonne che contengono 'geom' nel nome
        3. Analisi contenuto: colonne con valori che sembrano WKT, WKB hex o GeoJSON

    Args:
        df: DataFrame da analizzare.

    Returns:
        Nome della colonna geometria individuata, o None se non trovata.
    """
    # 1. Ricerca per nomi comuni (case-insensitive
    for nome_noto in GEOMETRY_COLUMN_NAMES:
        if nome_noto in df.columns:
            return nome_noto
        for col in df.columns:
            if col.lower() == nome_noto.lower():
                return col

    # 2. Ricerca per pattern nel nome (contiene 'geom'
    for col in df.columns:
        if "geom" in col.lower():
            return col

    # 3. Ricerca per contenuto (analisi del primo valore non nullo
    for col in df.columns:
        if df[col].dtype != "object":
            continue

        campione = df[col].dropna().iloc[0] if not df[col].dropna().empty else None
        if campione is None:
            continue

        campione_str = str(campione).strip()
        campione_upper = campione_str.upper()

        # Check WKT
        if any(campione_upper.startswith(prefisso) for prefisso in WKT_PREFIXES):
            return col

        # Check WKB hex
        if _sembra_wkb_hex(campione_str):
            return col

        # Check GeoJSON
        if campione_str.startswith("{"):
            try:
                geojson = json.loads(campione_str)
                if isinstance(geojson, dict) and "type" in geojson:
                    return col
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("detect_geometry_column: GeoJSON check fallito per colonna %s: %s", col, e)

    return None


# UTILITY DI CONVERSIONE

def ensure_same_crs(
    gdf1: gpd.GeoDataFrame,
    gdf2: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Allinea il CRS di due GeoDataFrame, riproiettando il secondo se necessario.

    Il primo GeoDataFrame è usato come riferimento: il secondo viene
    riproiettato al suo CRS se differente.

    Args:
        gdf1: GeoDataFrame di riferimento (il suo CRS viene mantenuto).
        gdf2: GeoDataFrame da allineare (verrà riproiettato se necessario).

    Returns:
        Tupla (gdf1, gdf2_riproiettato) con CRS identici.
    """
    if gdf1.crs != gdf2.crs:
        gdf2 = gdf2.to_crs(gdf1.crs)
    return gdf1, gdf2


def extract_srid_from_column_type(col_type: str) -> Optional[int]:
    """
    Estrae il codice SRID dal formato tipo colonna "geometry:subtype:srid".

    Il formato tipo usato internamente da PLENORA-ETL codifica il subtype e
    l'SRID separati da ':'. L'SRID è il terzo componente, se presente.

    Esempi:
        "geometry:polygon:4326" → 4326
        "geometry:point:3003" → 3003
        "geometry:polygon" → None (SRID non specificato)
        "str" → None (non è una geometria)

    Args:
        col_type: Stringa tipo colonna.

    Returns:
        SRID come intero, o None se non presente o non valido.
    """
    if not col_type or not col_type.startswith("geometry"):
        return None

    parti = col_type.split(":")
    if len(parti) >= 3:
        try:
            return int(parti[2])
        except ValueError as e:
            logger.debug("extract_srid_from_column_type: parsing SRID fallito per %r: %s", col_type, e)

    return None
