"""
Utility centralizzate per operazioni geospaziali in PLENORA-ETL.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import json
from typing import Optional, Any

import pandas as pd
import geopandas as gpd
import shapely
from shapely.geometry import shape as _shapely_shape

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.schema import canvas_type_to_pandas_dtype
from app.core.resources.column_types import normalize_canvas_type
from app.core.dataframes.geometry_parsing import (
    GEOMETRY_COLUMN_NAMES,
    WKT_PREFIXES,
    extract_srid_from_ewkb,
    looks_like_wkb_hex,
    parse_geometry as _parse_geometry,
    parse_geometry_series,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Public compatibility re-export used by features geo utils.
parse_geometry = _parse_geometry


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


def geodataframe_from_geojson_records(
    records: list[dict[str, Any]],
    geometries: list[Optional[dict]],
    *,
    crs: str = "EPSG:4326",
    drop_z: bool = False,
) -> Any:
    """Assembla un GeoDataFrame da record piatti + geometrie GeoJSON.

    ``records`` e ``geometries`` sono allineati per indice; le geometrie sono
    dict GeoJSON (o None). Usato dal path IMDF bundle per trasformare i layer
    appiattiti dal descrittore in GeoDataFrame senza uscire dal boundary
    dataframe. ``drop_z`` appiattisce 3D->2D (ArcGIS FeatureService 2D).
    """
    geoms: list[Any] = []
    for g in geometries:
        if not g:
            geoms.append(None)
            continue
        geom = _shapely_shape(g)
        if drop_z and getattr(geom, "has_z", False):
            geom = shapely.force_2d(geom)
        geoms.append(geom)

    if not records:
        return gpd.GeoDataFrame({"geometry": gpd.GeoSeries(geoms, crs=crs)}, crs=crs)

    df = pd.DataFrame.from_records(records)
    return gpd.GeoDataFrame(df, geometry=gpd.GeoSeries(geoms, crs=crs), crs=crs)


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


def schema_crs(canvas_type: Any) -> Optional[str]:
    """CRS dichiarato da un tipo ``geometry:<sub>:<srid>``, se presente."""
    srid = extract_srid_from_column_type(canvas_type) if isinstance(canvas_type, str) else None
    return f"EPSG:{srid}" if srid else None


def empty_frame_from_schema(
    columns: Any,
    *,
    relax_types: bool = False,
    assumed_crs: Optional[str] = None,
) -> Any:
    """Frame con zero righe, nomi (anche duplicati), ordine e dtype dello schema.

    Mappatura unica tipo logico -> dtype: ``canvas_type_to_pandas_dtype``; le
    geometrie diventano ``GeoSeries`` vuote con il CRS dello schema (o
    ``assumed_crs`` se ignoto) e il frame e' un GeoDataFrame con geometria
    attiva ``geometry`` se presente, altrimenti la prima. ``relax_types``:
    colonne non geometriche ``object``.
    """
    rows = [col for col in (columns or []) if hasattr(col, "get") and col.get("name") is not None]
    names = [str(col["name"]) for col in rows]
    values = []
    geometry_names = []
    crs = None
    for col in rows:
        kind = col.get("type") or "str"
        if normalize_canvas_type(kind if isinstance(kind, str) else None) == "geometry":
            column_crs = schema_crs(kind) or assumed_crs
            crs = crs or column_crs
            values.append(gpd.GeoSeries([], crs=column_crs))
            geometry_names.append(str(col["name"]))
        else:
            values.append(pd.Series(dtype="object" if relax_types else canvas_type_to_pandas_dtype(kind)))
    frame = pd.DataFrame(dict(enumerate(values)))
    frame.columns = names
    if not geometry_names:
        return frame
    active = "geometry" if "geometry" in geometry_names else geometry_names[0]
    if names.count(active) != 1:
        return frame
    return gpd.GeoDataFrame(frame, geometry=active, crs=crs)


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


    # Parsing di tutte le geometrie nella colonna (fast path vettoriale)
    geometrie = parse_geometry_series(df[geom_column])

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
            geometrie_ricostruite = parse_geometry_series(ricostruita)
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
    df_copia = copy_frame_for_update(df)
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
        if looks_like_wkb_hex(campione_str):
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
