"""
Utility centralizzate per operazioni geospaziali.

Questo modulo accorpa funzioni duplicate da:
- orchestrator_service.py (_to_geodataframe, parse_geometry)
- api_connection_service.py (_save_enrichment_output geometrie)
- query_executor.py (_save_geo parse_geometry)

USO:
    from utils.geo_utils import (
        parse_geometry,
        to_geodataframe,
        detect_geometry_column,
        geometry_to_wkt
    )
"""

import pandas as pd
import geopandas as gpd
from shapely import wkt, wkb
from shapely.geometry import shape
from typing import Optional, Union, Any, List
import json

from utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# COSTANTI
# =============================================================================

# Nomi comuni per colonne geometriche
GEOMETRY_COLUMN_NAMES = [
    'geometry', 'geom', 'the_geom', 'shape', 'wkb_geometry', 'wkt'
]

# Prefissi WKT validi
WKT_PREFIXES = (
    'POINT', 'LINESTRING', 'POLYGON', 
    'MULTIPOINT', 'MULTILINESTRING', 'MULTIPOLYGON', 
    'GEOMETRYCOLLECTION'
)


# =============================================================================
# PARSING GEOMETRIE
# =============================================================================

def parse_geometry(value: Any) -> Optional[Any]:
    """
    Parsa una geometria da vari formati.
    
    Supporta:
    - WKT (Well-Known Text): "POINT(1 2)"
    - WKB (Well-Known Binary) hex: "0101000000..."
    - EWKB (Extended WKB con SRID) hex
    - GeoJSON dict o string: {"type": "Point", "coordinates": [1, 2]}
    - Oggetti Shapely (restituiti as-is)
    
    Args:
        value: Valore da parsare
        
    Returns:
        Oggetto geometria Shapely, o None se non parsabile
    """
    # Gestisci None/NaN
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    
    if value == '' or value == 'None':
        return None
    
    # Se è già un oggetto geometria Shapely, restituiscilo
    if hasattr(value, 'geom_type'):
        return value
    
    # Converti in stringa per parsing
    value_str = str(value).strip()
    
    if not value_str:
        return None
    
    # 1. Prova GeoJSON (inizia con '{')
    if value_str.startswith('{'):
        try:
            geojson = json.loads(value_str)
            # Verifica che sia un GeoJSON geometry
            if isinstance(geojson, dict) and 'type' in geojson:
                return shape(geojson)
        except Exception:
            pass
    
    # 2. Prova WKT (inizia con nome tipo geometria)
    value_upper = value_str.upper()
    if any(value_upper.startswith(prefix) for prefix in WKT_PREFIXES):
        try:
            return wkt.loads(value_str)
        except Exception:
            pass
    
    # 3. Prova WKB/EWKB hex (inizia con 0 ed è esadecimale)
    if _looks_like_wkb_hex(value_str):
        geom = _parse_wkb_hex(value_str)
        if geom is not None:
            return geom
    
    # 4. Prova WKB bytes
    if isinstance(value, (bytes, memoryview)):
        try:
            return wkb.loads(bytes(value))
        except Exception:
            pass
    
    # 5. Ultimo tentativo: forza WKB hex
    try:
        return wkb.loads(value_str, hex=True)
    except Exception:
        pass
    
    logger.debug(f"Impossibile parsare geometria: {value_str[:50]}...")
    return None


def _looks_like_wkb_hex(value: str) -> bool:
    """Verifica se una stringa sembra essere WKB in formato hex."""
    if len(value) < 20:
        return False
    if not value.startswith('0'):
        return False
    # Verifica che i primi 20 caratteri siano hex
    return all(c in '0123456789ABCDEFabcdef' for c in value[:20])


def _parse_wkb_hex(value_str: str) -> Optional[Any]:
    """
    Parsa WKB o EWKB in formato hex.
    
    EWKB ha il flag SRID nei byte del tipo geometria.
    """
    # Prova prima come WKB/EWKB standard
    try:
        return wkb.loads(value_str, hex=True)
    except Exception:
        pass
    
    # Prova a gestire EWKB con SRID
    try:
        # EWKB format: byte_order (1) + type_with_srid_flag (4) + srid (4) + geometry
        # Il flag SRID è 0x20000000, quindi il byte 5 contiene '20' se c'è SRID
        if len(value_str) > 18 and value_str[8:10] == '20':
            # Rimuovi flag SRID e SRID value
            # Byte order (2 char) + type senza flag (8 char, rimuovi '20') + resto geometria
            clean_wkb = value_str[:2] + value_str[2:10].replace('20', '00') + value_str[18:]
            return wkb.loads(clean_wkb, hex=True)
    except Exception:
        pass
    
    return None


def extract_srid_from_ewkb(value_str: str) -> Optional[int]:
    """
    Estrae SRID da una stringa EWKB hex.
    
    Args:
        value_str: Stringa EWKB hex
        
    Returns:
        SRID come intero, o None se non trovato
    """
    if not _looks_like_wkb_hex(value_str):
        return None
    
    if len(value_str) < 18:
        return None
    
    try:
        # EWKB: SRID è nei bytes 10-18 (little endian)
        # Solo se c'è il flag SRID (0x20 nel tipo)
        if value_str[8:10] == '20':
            srid_hex = value_str[10:18]
            srid_bytes = bytes.fromhex(srid_hex)
            srid = int.from_bytes(srid_bytes, byteorder='little')
            if srid > 0:
                return srid
    except Exception:
        pass
    
    return None


# =============================================================================
# CONVERSIONE GEODATAFRAME
# =============================================================================

def to_geodataframe(
    df: pd.DataFrame,
    geom_column: Optional[str] = None,
    crs: str = "EPSG:4326"
) -> gpd.GeoDataFrame:
    """
    Converte un DataFrame pandas in GeoDataFrame.
    
    Args:
        df: DataFrame da convertire
        geom_column: Nome colonna geometria (opzionale, auto-detect)
        crs: Sistema di riferimento (default WGS84)
        
    Returns:
        GeoDataFrame
        
    Raises:
        ValueError: Se nessuna colonna geometria trovata/valida
    """
    # Se è già un GeoDataFrame, restituiscilo
    if isinstance(df, gpd.GeoDataFrame):
        return df
    
    # Auto-detect colonna geometria
    if geom_column is None:
        geom_column = detect_geometry_column(df)
    
    if geom_column is None or geom_column not in df.columns:
        raise ValueError(
            f"Nessuna colonna geometria trovata. "
            f"Colonne disponibili: {list(df.columns)}"
        )
    
    logger.debug(f"Usando colonna geometria: {geom_column}")
    
    # Parsa geometrie
    geometries = df[geom_column].apply(parse_geometry)
    
    # Conta geometrie valide
    valid_count = geometries.notna().sum()
    if valid_count == 0:
        raise ValueError(f"Nessuna geometria valida nella colonna '{geom_column}'")
    
    logger.debug(f"Geometrie valide: {valid_count}/{len(df)}")
    
    # Prova a estrarre SRID dalla prima geometria EWKB
    detected_crs = crs
    first_value = df[geom_column].dropna().iloc[0] if len(df[geom_column].dropna()) > 0 else None
    if first_value:
        srid = extract_srid_from_ewkb(str(first_value))
        if srid:
            detected_crs = f"EPSG:{srid}"
            logger.debug(f"Rilevato SRID: {srid}")
    
    # Crea GeoDataFrame
    # Rimuovi colonna geometria originale se diversa da 'geometry'
    df_copy = df.drop(columns=[geom_column]) if geom_column != 'geometry' else df.copy()
    
    gdf = gpd.GeoDataFrame(df_copy, geometry=geometries.values, crs=detected_crs)
    
    logger.info(f"Convertito in GeoDataFrame: {len(gdf)} righe, CRS: {gdf.crs}")
    return gdf


def detect_geometry_column(df: pd.DataFrame) -> Optional[str]:
    """
    Rileva automaticamente la colonna geometria in un DataFrame.
    
    Cerca:
    1. Colonne con nomi comuni (geometry, geom, the_geom, shape, wkt)
    2. Colonne che contengono 'geom' nel nome
    3. Colonne con valori che sembrano geometrie (WKT, WKB)
    
    Args:
        df: DataFrame da analizzare
        
    Returns:
        Nome della colonna geometria, o None se non trovata
    """
    # 1. Cerca nomi esatti comuni
    for col_name in GEOMETRY_COLUMN_NAMES:
        if col_name in df.columns:
            return col_name
        # Case-insensitive
        for col in df.columns:
            if col.lower() == col_name.lower():
                return col
    
    # 2. Cerca colonne che contengono 'geom'
    for col in df.columns:
        if 'geom' in col.lower():
            return col
    
    # 3. Cerca colonne con valori che sembrano geometrie
    for col in df.columns:
        if df[col].dtype == 'object':
            sample = df[col].dropna().iloc[0] if not df[col].dropna().empty else None
            if sample:
                sample_str = str(sample).strip().upper()
                # Check WKT
                if any(sample_str.startswith(prefix) for prefix in WKT_PREFIXES):
                    return col
                # Check WKB hex
                if _looks_like_wkb_hex(str(sample)):
                    return col
                # Check GeoJSON
                if str(sample).strip().startswith('{'):
                    try:
                        geojson = json.loads(str(sample))
                        if isinstance(geojson, dict) and 'type' in geojson:
                            return col
                    except:
                        pass
    
    return None


# =============================================================================
# UTILITY GEOMETRIE
# =============================================================================

def geometry_to_wkt(geom: Any) -> Optional[str]:
    """
    Converte una geometria in formato WKT.
    
    Args:
        geom: Oggetto geometria Shapely
        
    Returns:
        Stringa WKT, o None se la geometria è None
    """
    if geom is None:
        return None
    if hasattr(geom, 'wkt'):
        return geom.wkt
    return None


def geometry_to_geojson(geom: Any) -> Optional[dict]:
    """
    Converte una geometria in formato GeoJSON dict.
    
    Args:
        geom: Oggetto geometria Shapely
        
    Returns:
        Dict GeoJSON, o None se la geometria è None
    """
    if geom is None:
        return None
    if hasattr(geom, '__geo_interface__'):
        return geom.__geo_interface__
    return None


def geodataframe_to_dataframe_wkt(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Converte un GeoDataFrame in DataFrame con geometrie come WKT.
    
    Utile per salvare in formati non-geo (CSV, JSON) mantenendo le geometrie.
    
    Args:
        gdf: GeoDataFrame da convertire
        
    Returns:
        DataFrame con colonna geometry come stringhe WKT
    """
    df = pd.DataFrame(gdf)
    
    if 'geometry' in df.columns:
        df['geometry'] = df['geometry'].apply(geometry_to_wkt)
    
    return df


def ensure_same_crs(gdf1: gpd.GeoDataFrame, gdf2: gpd.GeoDataFrame) -> tuple:
    """
    Assicura che due GeoDataFrame abbiano lo stesso CRS.
    Riproietta gdf2 al CRS di gdf1 se necessario.
    
    Args:
        gdf1: Primo GeoDataFrame (CRS di riferimento)
        gdf2: Secondo GeoDataFrame (da riproiettare se necessario)
        
    Returns:
        Tuple (gdf1, gdf2_reprojected)
    """
    if gdf1.crs != gdf2.crs:
        logger.debug(f"Riproiezione: {gdf2.crs} -> {gdf1.crs}")
        gdf2 = gdf2.to_crs(gdf1.crs)
    
    return gdf1, gdf2


def get_utm_crs_for_geometry(gdf: gpd.GeoDataFrame) -> str:
    """
    Determina il CRS UTM appropriato per un GeoDataFrame.
    Utile per operazioni che richiedono unità metriche (buffer, area).
    
    Args:
        gdf: GeoDataFrame da analizzare
        
    Returns:
        Stringa CRS UTM (es. "EPSG:32632")
    """
    if gdf.empty:
        return "EPSG:32632"  # Default UTM zone 32N
    
    # Calcola centroide dell'unione di tutte le geometrie
    try:
        centroid = gdf.geometry.unary_union.centroid
        lon = centroid.x
        lat = centroid.y
        
        # Calcola zona UTM
        utm_zone = int((lon + 180) / 6) + 1
        
        # Nord o Sud
        if lat >= 0:
            return f"EPSG:326{utm_zone:02d}"
        else:
            return f"EPSG:327{utm_zone:02d}"
    except:
        return "EPSG:32632"  # Default


def extract_srid_from_column_type(col_type: str) -> Optional[int]:
    """
    Estrae SRID dal tipo colonna nel formato geometry:subtype:srid.
    
    Es: "geometry:polygon:4326" -> 4326
    
    Args:
        col_type: Tipo colonna
        
    Returns:
        SRID come intero, o None se non presente
    """
    if not col_type or not col_type.startswith('geometry'):
        return None
    
    parts = col_type.split(':')
    if len(parts) >= 3:
        try:
            return int(parts[2])
        except ValueError:
            pass
    
    return None
