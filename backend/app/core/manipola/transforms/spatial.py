"""
Operazioni di trasformazione geospaziali per MANIPOLA.

Architettura
============
Questo modulo contiene le funzioni di trasformazione geospaziali registrate
nel transforms_registry.json. Tutte le operazioni lavorano su GeoDataFrame
e gestiscono automaticamente la conversione CRS quando necessario.

Convenzione funzioni:
- Input singolo: op_geo_xxx(gdf: GeoDataFrame, **kwargs) -> GeoDataFrame
- Input multiplo: op_geo_xxx(gdfs: List[GeoDataFrame], **kwargs) -> GeoDataFrame
- I parametri arrivano come kwargs espliciti (preparati da PluginExecutor)
- Le funzioni NON modificano l'input: lavorano su copie

Catalogo operazioni geospaziali:
- BUFFER: op_geo_buffer (buffer con distanza in metri, riproiezione automatica)
- CENTROID: op_geo_centroid (centroide di ogni geometria)
- AREA: op_geo_area (calcolo area in m², riproiezione se necessario)
- LENGTH: op_geo_length (lunghezza linee in metri)
- SIMPLIFY: op_geo_simplify (semplificazione geometrie con tolleranza)
- CONVEX HULL: op_geo_convex_hull (inviluppo convesso)
- DISSOLVE: op_geo_dissolve (fusione geometrie per attributo con aggregazione)
- CLIP: op_geo_clip (ritaglio geometrie con maschera)
- REPROJECT: op_geo_reproject (cambio sistema di riferimento CRS)
- SPATIAL JOIN: op_geo_sjoin (join spaziale: intersects, contains, within)
- NEAREST: op_geo_nearest (join per vicinanza con distanza max opzionale)
- DISTANCE: op_geo_distance (calcolo distanza tra punti di due layer)
- OVERLAY: op_geo_overlay (operazioni insiemistiche: intersection, union, difference)

Gestione CRS:
- Le operazioni metriche (buffer, area, length) riproiettano automaticamente
  in un CRS metrico appropriato (UTM o EPSG:3857) se l'input è in gradi
- Le operazioni multi-layer allineano automaticamente i CRS prima dell'operazione

Dipendenze: GeoPandas, Shapely, pyproj

Uso diretto (per test):
    from app.core.manipola.transforms.spatial import op_geo_buffer
    result = op_geo_buffer(gdf, distance=100, cap_style="round")
"""

import pandas as pd
import geopandas as gpd
from typing import List, Optional, Union

from app.utils.logger import get_logger
from app.utils.geo import (
    to_geodataframe,
    ensure_same_crs,
    get_utm_crs_for_geometry
)

# Shapely imports con fallback per versioni diverse
try:
    from shapely.validation import make_valid as _make_valid
    HAS_MAKE_VALID = True
except ImportError:
    HAS_MAKE_VALID = False

try:
    from shapely.ops import voronoi_diagram as _voronoi_diagram
    HAS_VORONOI = True
except ImportError:
    HAS_VORONOI = False

try:
    import shapely
    HAS_SHAPELY2 = hasattr(shapely, 'get_num_coordinates')
except ImportError:
    HAS_SHAPELY2 = False

from shapely.geometry import LineString, Polygon

logger = get_logger(__name__)

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


def _ensure_active_geometry(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Assicura che il GeoDataFrame abbia una geometria attiva valida.
    
    Dopo operazioni come sjoin, se entrambi i dataframe avevano una colonna
    'geometry', una può essere rinominata (es. 'geometry_right', 'geometry_roma')
    e il GeoDataFrame perde il riferimento alla geometria attiva.
    
    Questa funzione trova la prima colonna geometry disponibile e la imposta
    come geometria attiva.
    """
    try:
        # Se la geometria attiva esiste nelle colonne, tutto ok
        if gdf.geometry.name in gdf.columns:
            return gdf
    except Exception:
        pass

    # Trova colonne di tipo geometry
    geom_cols = []
    for col in gdf.columns:
        try:
            if hasattr(gdf[col], 'geom_type') or gdf[col].dtype.name == 'geometry':
                geom_cols.append(col)
        except Exception:
            continue
    
    if geom_cols:
        # Preferisci 'geometry' se esiste, altrimenti la prima trovata
        if 'geometry' in geom_cols:
            gdf = gdf.set_geometry('geometry')
        else:
            gdf = gdf.set_geometry(geom_cols[0])
        logger.info(f"Geometria attiva impostata su: {gdf.geometry.name}")
    
    return gdf


# =============================================================================
# CRS HELPERS — supporto parametri opt-in output_crs / metric_crs
# =============================================================================
# Opt-in API: gli operator metrici (buffer/centroid/area/length/perimeter/
# distance/nearest) accettano due parametri opzionali per controllare
# esplicitamente il CRS di calcolo e il CRS di output geometrico.
#
#   metric_crs:
#     - None (default) -> auto-UTM via get_utm_crs_for_geometry se l'input
#                         e' in CRS geografico, altrimenti nessuna reproject
#     - "EPSG:xxxx"    -> usa quel CRS per il calcolo metrico (utile per
#                         dataset multi-zona: Lambert conformico, LAEA)
#   output_crs (solo operator con output geometrico):
#     - "same"  (default) -> ritorna geometrie nel CRS dell'input
#     - "metric"          -> ritorna geometrie nel metric_crs usato
#                            (evita roundtrip)
#     - "EPSG:xxxx"       -> riproietta esplicitamente al CRS specificato
#
# Invariante backward-compat: omettere entrambi e' identico al comportamento
# pre-refactor.


def _resolve_metric_crs(
    gdf: gpd.GeoDataFrame, metric_crs: Optional[str]
) -> Optional[str]:
    """CRS da usare per il calcolo metrico.

    Returns:
        - metric_crs (stripped) se passato non-vuoto
        - auto-UTM se gdf e' in CRS geografico
        - None se gdf e' gia' in CRS metrico (nessuna reproject necessaria)
    """
    if metric_crs:
        s = metric_crs.strip() if isinstance(metric_crs, str) else None
        if s:
            return s
    if gdf.crs is not None and gdf.crs.is_geographic:
        return get_utm_crs_for_geometry(gdf)
    return None


def _resolve_output_geom_crs(
    original_crs, metric_used: Optional[str], output_crs: str
) -> Optional[str]:
    """CRS finale per le geometrie di output.

    - "same" (default): CRS dell'input (retrocompat)
    - "metric": metric_used se presente, altrimenti original_crs
    - "EPSG:xxxx": interpretato come CRS target
    """
    if output_crs is None or output_crs == "same":
        return original_crs
    if output_crs == "metric":
        return metric_used if metric_used is not None else original_crs
    return output_crs


# =============================================================================
# JOIN SPAZIALE
# Unisce due GeoDataFrame per relazione spaziale (intersects, contains, within).
# =============================================================================

def op_sjoin(
    inputs: List[DataFrame],
    left_geom: str,
    right_geom: str,
    predicate: str = "intersects",
    how: str = "inner"
) -> gpd.GeoDataFrame:
    """
    Join spaziale tra due GeoDataFrame.
    
    Args:
        inputs: Lista di 2 DataFrame [left, right]
        left_geom: Colonna geometria del left
        right_geom: Colonna geometria del right
        predicate: Predicato spaziale (intersects, contains, within, crosses, overlaps, touches)
        how: Tipo join (inner, left)
        
    Returns:
        GeoDataFrame con join spaziale
    """
    if len(inputs) < 2:
        logger.warning("Spatial Join richiede 2 input")
        return inputs[0] if inputs else gpd.GeoDataFrame()
    
    try:
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else to_geodataframe(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else to_geodataframe(inputs[1], right_geom)

        # Allinea CRS
        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.sjoin(left_gdf, right_gdf, how=how, predicate=predicate)
        
        # Rimuovi colonna index_right
        if 'index_right' in result.columns:
            result = result.drop(columns=['index_right'])
        
        # FIX: Assicura che ci sia una geometria attiva valida
        result = _ensure_active_geometry(result)
        
        logger.info(f"Spatial Join completato: {len(result)} righe, predicate={predicate}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Spatial Join: {e}", exc_info=True)
        return inputs[0] if inputs else gpd.GeoDataFrame()


# =============================================================================
# BUFFER
# Crea area di rispetto attorno alle geometrie. Distanza in metri, riproiezione automatica.
# =============================================================================

def op_geo_buffer(
    df: DataFrame,
    geom_column: str,
    distance: float,
    output_column: str = "buffer_geom",
    cap_style: str = "round",
    metric_crs: Optional[str] = None,
    output_crs: str = "same",
) -> gpd.GeoDataFrame:
    """
    Crea buffer attorno alle geometrie.

    Args:
        df: DataFrame/GeoDataFrame input
        geom_column: Colonna geometria sorgente
        distance: Distanza buffer in unita' del CRS metrico (metri se auto-UTM)
        output_column: Nome colonna output
        cap_style: Stile terminazione (round, flat, square)
        metric_crs: CRS per calcolo metrico. None -> auto-UTM se geografico.
        output_crs: "same" (default, CRS input), "metric" (no roundtrip),
                    "EPSG:xxxx" (riproiezione esplicita)

    Returns:
        GeoDataFrame con colonna buffer
    """
    cap_style_map = {'round': 1, 'flat': 2, 'square': 3}
    cap_style_int = cap_style_map.get(cap_style, 1)

    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        original_crs = gdf.crs

        metric_used = _resolve_metric_crs(gdf, metric_crs)
        if metric_used is not None:
            gdf_projected = gdf.to_crs(metric_used)
        else:
            gdf_projected = gdf

        buffer_geom = gdf_projected.geometry.buffer(distance, cap_style=cap_style_int)

        target_crs = _resolve_output_geom_crs(original_crs, metric_used, output_crs)
        current_geom_crs = metric_used if metric_used is not None else original_crs
        if target_crs is not None and str(target_crs) != str(current_geom_crs):
            buffer_gdf = gpd.GeoDataFrame(geometry=buffer_geom, crs=current_geom_crs)
            buffer_geom = buffer_gdf.to_crs(target_crs).geometry

        result = gdf.copy()
        result[output_column] = buffer_geom.values

        logger.info(f"Buffer completato: {distance}m, output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Buffer: {e}", exc_info=True)
        return df


# =============================================================================
# CENTROIDE
# Calcola il punto centrale di ogni geometria.
# =============================================================================

def op_geo_centroid(
    df: DataFrame,
    geom_column: str,
    output_column: str = "centroid",
    metric_crs: Optional[str] = None,
    output_crs: str = "same",
) -> gpd.GeoDataFrame:
    """
    Calcola il centroide delle geometrie.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output
        metric_crs: CRS per calcolo metrico. None -> auto-UTM se geografico.
        output_crs: "same" | "metric" | "EPSG:xxxx"

    Returns:
        GeoDataFrame con colonna centroide
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        original_crs = gdf.crs

        metric_used = _resolve_metric_crs(gdf, metric_crs)
        if metric_used is not None:
            gdf_projected = gdf.to_crs(metric_used)
            centroids = gdf_projected.geometry.centroid
        else:
            centroids = gdf.geometry.centroid

        target_crs = _resolve_output_geom_crs(original_crs, metric_used, output_crs)
        current_geom_crs = metric_used if metric_used is not None else original_crs
        if target_crs is not None and str(target_crs) != str(current_geom_crs):
            centroid_gdf = gpd.GeoDataFrame(geometry=centroids, crs=current_geom_crs)
            centroids = centroid_gdf.to_crs(target_crs).geometry

        result = gdf.copy()
        result[output_column] = centroids.values

        logger.info(f"Centroid completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Centroid: {e}", exc_info=True)
        return df


# =============================================================================
# AREA
# Calcola area in m². Riproietta automaticamente in CRS metrico se necessario.
# =============================================================================

def op_geo_area(
    df: DataFrame,
    geom_column: str,
    output_column: str = "area_m2",
    metric_crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Calcola l'area in unita' quadrate del CRS metrico (m^2 di default).

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output
        metric_crs: CRS per calcolo area. None -> auto-UTM se geografico.

    Returns:
        GeoDataFrame con colonna area
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        metric_used = _resolve_metric_crs(gdf, metric_crs)
        if metric_used is not None:
            gdf_projected = gdf.to_crs(metric_used)
        else:
            gdf_projected = gdf

        result = gdf.copy()
        result[output_column] = gdf_projected.geometry.area
        
        logger.info(f"Area completato: output={output_column}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Area: {e}", exc_info=True)
        return df


# =============================================================================
# RITAGLIO (CLIP)
# Ritaglia le geometrie del primo layer usando il secondo come maschera.
# =============================================================================

def op_geo_clip(
    inputs: List[DataFrame],
    input_geom: str,
    mask_geom: str
) -> gpd.GeoDataFrame:
    """
    Ritaglia geometrie usando una maschera.
    
    Args:
        inputs: Lista [geometrie_da_ritagliare, maschera]
        input_geom: Colonna geometria del primo input
        mask_geom: Colonna geometria della maschera
        
    Returns:
        GeoDataFrame ritagliato
    """
    if len(inputs) < 2:
        logger.warning("Clip richiede 2 input")
        return inputs[0] if inputs else gpd.GeoDataFrame()
    
    try:
        input_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else to_geodataframe(inputs[0], input_geom)
        mask_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else to_geodataframe(inputs[1], mask_geom)
        
        # Allinea CRS
        input_gdf, mask_gdf = ensure_same_crs(input_gdf, mask_gdf)
        
        result = gpd.clip(input_gdf, mask_gdf)
        
        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)
        
        logger.info(f"Clip completato: {len(result)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Clip: {e}", exc_info=True)
        return inputs[0] if inputs else gpd.GeoDataFrame()


# =============================================================================
# DISSOLVE (FUSIONE)
# Fonde geometrie raggruppate per attributo con funzione di aggregazione.
# =============================================================================

def op_geo_dissolve(
    df: DataFrame,
    geom_column: str,
    by_column: str = None,
    aggfunc: str = "first"
) -> gpd.GeoDataFrame:
    """
    Dissolve geometrie (unione spaziale con raggruppamento).
    
    Args:
        df: DataFrame input
        geom_column: Colonna geometria
        by_column: Colonna per raggruppamento (None = dissolvi tutto)
        aggfunc: Funzione aggregazione per attributi (first, last, sum, mean, count)
        
    Returns:
        GeoDataFrame dissolto
    """
    # aggfunc che non supportano colonne stringa: applicarle solo alle numeriche
    NUMERIC_ONLY_AGGFUNCS = {"mean", "sum", "std", "var", "median"}

    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        if aggfunc in NUMERIC_ONLY_AGGFUNCS:
            # Costruisce un dict per colonna: aggfunc sulle numeriche, "first" sulle stringhe
            geom_name = gdf.geometry.name
            col_aggfunc = {}
            for col in gdf.columns:
                if col == geom_name:
                    continue
                if by_column and col == by_column:
                    continue
                if gdf[col].dtype.kind in ("i", "u", "f"):   # int, uint, float
                    col_aggfunc[col] = aggfunc
                else:
                    col_aggfunc[col] = "first"
            effective_aggfunc = col_aggfunc if col_aggfunc else "first"
        else:
            effective_aggfunc = aggfunc

        if by_column and by_column in gdf.columns:
            result = gdf.dissolve(by=by_column, aggfunc=effective_aggfunc).reset_index()
        else:
            result = gdf.dissolve(aggfunc=effective_aggfunc).reset_index()

        logger.info(f"Dissolve completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Dissolve: {e}", exc_info=True)
        return df


# =============================================================================
# INTERSEZIONE
# Operazione insiemistica: area comune tra due layer.
# =============================================================================

def op_geo_intersection(
    inputs: List[DataFrame],
    left_geom: str,
    right_geom: str
) -> gpd.GeoDataFrame:
    """
    Intersezione geometrica tra due layer.
    
    Args:
        inputs: Lista [layer1, layer2]
        left_geom: Colonna geometria del primo layer
        right_geom: Colonna geometria del secondo layer
        
    Returns:
        GeoDataFrame con intersezione
    """
    if len(inputs) < 2:
        return inputs[0] if inputs else gpd.GeoDataFrame()
    
    try:
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else to_geodataframe(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else to_geodataframe(inputs[1], right_geom)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='intersection', keep_geom_type=False)
        
        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)
        
        logger.info(f"Intersection completato: {len(result)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Intersection: {e}", exc_info=True)
        return inputs[0] if inputs else gpd.GeoDataFrame()


# =============================================================================
# UNIONE GEOMETRICA
# Operazione insiemistica: unione delle aree di due layer.
# =============================================================================

def op_geo_union(
    inputs: List[DataFrame],
    left_geom: str,
    right_geom: str
) -> gpd.GeoDataFrame:
    """
    Unione geometrica di due layer.
    
    Args:
        inputs: Lista [layer1, layer2]
        left_geom: Colonna geometria del primo layer
        right_geom: Colonna geometria del secondo layer
        
    Returns:
        GeoDataFrame con unione
    """
    if len(inputs) < 2:
        return inputs[0] if inputs else gpd.GeoDataFrame()
    
    try:
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else to_geodataframe(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else to_geodataframe(inputs[1], right_geom)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='union', keep_geom_type=False)
        
        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)
        
        logger.info(f"Union geometrica completata: {len(result)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Union geometrica: {e}", exc_info=True)
        return inputs[0] if inputs else gpd.GeoDataFrame()


# =============================================================================
# DIFFERENZA
# Operazione insiemistica: aree del primo layer NON coperte dal secondo.
# =============================================================================

def op_geo_difference(
    inputs: List[DataFrame],
    left_geom: str,
    right_geom: str
) -> gpd.GeoDataFrame:
    """
    Differenza geometrica (A - B).
    
    Args:
        inputs: Lista [layer_A, layer_B]
        left_geom: Colonna geometria di A
        right_geom: Colonna geometria di B
        
    Returns:
        GeoDataFrame con differenza
    """
    if len(inputs) < 2:
        return inputs[0] if inputs else gpd.GeoDataFrame()
    
    try:
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else to_geodataframe(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else to_geodataframe(inputs[1], right_geom)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='difference', keep_geom_type=False)
        
        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)
        
        logger.info(f"Difference completato: {len(result)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Difference: {e}", exc_info=True)
        return inputs[0] if inputs else gpd.GeoDataFrame()


# =============================================================================
# SEMPLIFICAZIONE
# Riduce complessità geometrie mantenendo la forma (algoritmo Douglas-Peucker).
# =============================================================================

def op_geo_simplify(
    df: DataFrame,
    geom_column: str,
    tolerance: float = 0.001,
    preserve_topology: bool = True
) -> gpd.GeoDataFrame:
    """
    Semplifica geometrie (Douglas-Peucker).
    
    Args:
        df: DataFrame input
        geom_column: Colonna geometria
        tolerance: Tolleranza semplificazione (unità del CRS)
        preserve_topology: Se True, evita auto-intersezioni
        
    Returns:
        GeoDataFrame con geometrie semplificate
    """
    if tolerance is None or tolerance <= 0:
        raise ValueError(f"Simplify tolerance deve essere > 0, ricevuto: {tolerance}")

    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        gdf['geometry'] = gdf.geometry.simplify(tolerance, preserve_topology=preserve_topology)

        logger.info(f"Simplify completato: tolerance={tolerance}")
        return gdf
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Simplify: {e}", exc_info=True)
        return df


# =============================================================================
# INVILUPPO CONVESSO
# Calcola il poligono convesso minimo che contiene ogni geometria.
# =============================================================================

def op_geo_convex_hull(
    df: DataFrame,
    geom_column: str,
    output_column: str = "convex_hull"
) -> gpd.GeoDataFrame:
    """
    Calcola l'inviluppo convesso delle geometrie.
    
    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output
        
    Returns:
        GeoDataFrame con colonna convex hull
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        convex_hulls = gdf.geometry.convex_hull
        
        result = gdf.copy()
        result[output_column] = convex_hulls.values
        
        logger.info(f"Convex Hull completato: output={output_column}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Convex Hull: {e}", exc_info=True)
        return df


# =============================================================================
# RIPROIEZIONE CRS
# Cambia sistema di coordinate (es: WGS84 → UTM 32N, EPSG:4326 → EPSG:32632).
# =============================================================================

def op_geo_reproject(
    df: DataFrame,
    geom_column: str,
    target_crs: str = "EPSG:4326",
    source_crs: str = None
) -> gpd.GeoDataFrame:
    """
    Riproietta geometrie in un nuovo CRS.
    
    Args:
        df: DataFrame input
        geom_column: Colonna geometria
        target_crs: CRS destinazione (es: "EPSG:4326", "EPSG:32632")
        source_crs: CRS sorgente opzionale (es: "EPSG:32632"). Se fornito,
                    sovrascrive l'auto-rilevamento. Utile quando i dati
                    provengono da CSV (che non contiene metadati CRS).
        
    Returns:
        GeoDataFrame riproiettato
    """
    try:
        # Passa source_crs a to_geodataframe se specificato
        crs_hint = source_crs if source_crs else "EPSG:4326"
        gdf = to_geodataframe(df, geom_column, crs=crs_hint)
        
        # Se source_crs è fornito esplicitamente, forzalo (override auto-detect)
        if source_crs and gdf.crs and str(gdf.crs) != source_crs:
            logger.info(f"Override CRS sorgente: {gdf.crs} -> {source_crs}")
            gdf = gdf.set_crs(source_crs, allow_override=True)
        
        if gdf.crs is None:
            gdf = gdf.set_crs(crs_hint)
        
        original_crs = gdf.crs
        result = gdf.to_crs(target_crs)
        
        logger.info(f"Reproject completato: {original_crs} -> {target_crs}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Reproject: {e}", exc_info=True)
        return df

# =============================================================================
# BOUNDING BOX (INVILUPPO RETTANGOLARE)
# Calcola il rettangolo minimo che contiene ogni geometria.
# =============================================================================

def op_geo_envelope(
    df: DataFrame,
    geom_column: str,
    output_column: str = "envelope"
) -> gpd.GeoDataFrame:
    """
    Calcola il bounding box (rettangolo minimo) delle geometrie.
    
    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output
        
    Returns:
        GeoDataFrame con colonna envelope
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        envelopes = gdf.geometry.envelope
        
        result = gdf.copy()
        result[output_column] = envelopes.values
        
        logger.info(f"Envelope completato: output={output_column}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Envelope: {e}", exc_info=True)
        return df


# =============================================================================
# DISTANZA TRA GEOMETRIE
# Calcola la distanza tra geometrie di due layer in metri.
# =============================================================================

def op_geo_distance(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str,
    output_column: str = "distance_m",
    metric_crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Calcola la distanza tra geometrie di due layer.

    Per ogni riga del layer sinistro, calcola la distanza minima
    verso le geometrie del layer destro nel CRS metrico scelto.

    Args:
        dfs: Lista [layer_sinistro, layer_destro]
        left_geom_column: Colonna geometria del layer sinistro
        right_geom_column: Colonna geometria del layer destro
        output_column: Nome colonna distanza output
        metric_crs: CRS per calcolo distanza. None -> auto-UTM se geografico.

    Returns:
        GeoDataFrame del layer sinistro con colonna distanza
    """
    if len(dfs) < 2:
        logger.warning("Distance richiede 2 input")
        return dfs[0] if dfs else gpd.GeoDataFrame()

    try:
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else to_geodataframe(dfs[1], right_geom_column)

        # Allinea CRS
        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        metric_used = _resolve_metric_crs(left_gdf, metric_crs)
        if metric_used is not None:
            left_proj = left_gdf.to_crs(metric_used)
            right_proj = right_gdf.to_crs(metric_used)
        else:
            left_proj = left_gdf
            right_proj = right_gdf

        # Unisci tutte le geometrie del right in un unico oggetto per distanza
        if len(right_proj) == 0:
            logger.warning("Distance: right_gdf vuoto, output column = None")
            result = left_gdf.copy()
            result[output_column] = None
            return result
        right_union = right_proj.geometry.unary_union

        result = left_gdf.copy()
        result[output_column] = left_proj.geometry.distance(right_union)

        logger.info(f"Distance completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Distance: {e}", exc_info=True)
        return dfs[0] if dfs else gpd.GeoDataFrame()


# =============================================================================
# NEAREST NEIGHBOR JOIN
# Join per vicinanza: associa ad ogni geometria il vicino più prossimo.
# =============================================================================

def op_geo_nearest(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str,
    max_distance: Optional[float] = None,
    output_column: str = "distance_m",
    metric_crs: Optional[str] = None,
    output_crs: str = "same",
) -> gpd.GeoDataFrame:
    """
    Nearest neighbor join tra due layer.

    Per ogni geometria del layer sinistro, trova il vicino più prossimo
    nel layer destro e ne riporta gli attributi con la distanza.

    Args:
        dfs: Lista [layer_sinistro, layer_destro]
        left_geom_column: Colonna geometria del layer sinistro
        right_geom_column: Colonna geometria del layer destro
        max_distance: Distanza massima in unità del CRS metrico scelto.
        output_column: Nome colonna distanza output
        metric_crs: CRS per calcolo distanza. None -> auto-UTM se geografico.
        output_crs: "same" | "metric" | "EPSG:xxxx"

    Returns:
        GeoDataFrame con attributi del vicino più prossimo e distanza
    """
    if len(dfs) < 2:
        logger.warning("Nearest richiede 2 input")
        return dfs[0] if dfs else gpd.GeoDataFrame()

    try:
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else to_geodataframe(dfs[1], right_geom_column)

        if len(left_gdf) == 0 or len(right_gdf) == 0:
            logger.warning(f"Nearest: input vuoto (left={len(left_gdf)}, right={len(right_gdf)})")
            return left_gdf

        # Allinea CRS
        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        original_crs = left_gdf.crs
        metric_used = _resolve_metric_crs(left_gdf, metric_crs)
        if metric_used is not None:
            left_proj = left_gdf.to_crs(metric_used)
            right_proj = right_gdf.to_crs(metric_used)
        else:
            left_proj = left_gdf
            right_proj = right_gdf

        # sjoin_nearest con distanza
        result = gpd.sjoin_nearest(
            left_proj,
            right_proj,
            how="left",
            max_distance=max_distance,
            distance_col=output_column
        )

        # Rimuovi colonna index_right se presente
        if 'index_right' in result.columns:
            result = result.drop(columns=['index_right'])

        target_crs = _resolve_output_geom_crs(original_crs, metric_used, output_crs)
        current_geom_crs = metric_used if metric_used is not None else original_crs
        if target_crs is not None and str(target_crs) != str(current_geom_crs):
            result = result.to_crs(target_crs)

        # Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Nearest completato: {len(result)} righe, output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Nearest: {e}", exc_info=True)
        return dfs[0] if dfs else gpd.GeoDataFrame()


# =============================================================================
# LUNGHEZZA LINEE
# Calcola la lunghezza in metri di geometrie lineari.
# =============================================================================

def op_geo_length(
    df: DataFrame,
    geom_column: str,
    output_column: str = "length_m",
    metric_crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Calcola la lunghezza di geometrie lineari in unita' del CRS metrico.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output
        metric_crs: CRS per calcolo lunghezza. None -> auto-UTM se geografico.

    Returns:
        GeoDataFrame con colonna lunghezza
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        metric_used = _resolve_metric_crs(gdf, metric_crs)
        if metric_used is not None:
            gdf_projected = gdf.to_crs(metric_used)
        else:
            gdf_projected = gdf

        result = gdf.copy()
        result[output_column] = gdf_projected.geometry.length

        logger.info(f"Length completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Length: {e}", exc_info=True)
        return df


# =============================================================================
# PERIMETRO POLIGONI
# Calcola il perimetro in metri dei poligoni.
# =============================================================================

def op_geo_perimeter(
    df: DataFrame,
    geom_column: str,
    output_column: str = "perimeter_m",
    metric_crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Calcola il perimetro di geometrie poligonali in unita' del CRS metrico.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output
        metric_crs: CRS per calcolo perimetro. None -> auto-UTM se geografico.

    Returns:
        GeoDataFrame con colonna perimetro
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        metric_used = _resolve_metric_crs(gdf, metric_crs)
        if metric_used is not None:
            gdf_projected = gdf.to_crs(metric_used)
        else:
            gdf_projected = gdf

        # Per i poligoni, geometry.length restituisce il perimetro
        result = gdf.copy()
        result[output_column] = gdf_projected.geometry.length

        logger.info(f"Perimeter completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Perimeter: {e}", exc_info=True)
        return df


# =============================================================================
# ESPLODI MULTI-GEOMETRIE
# Converte MultiPolygon/MultiLineString/MultiPoint in geometrie singole.
# =============================================================================

def op_geo_explode(
    df: DataFrame,
    geom_column: str
) -> gpd.GeoDataFrame:
    """
    Esplode multi-geometrie in geometrie singole.

    Converte MultiPolygon in Polygon, MultiLineString in LineString, ecc.
    Ogni parte diventa una riga separata con gli stessi attributi.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente

    Returns:
        GeoDataFrame con geometrie singole
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        result = gdf.explode(index_parts=False).reset_index(drop=True)

        logger.info(f"Explode completato: {len(gdf)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Explode: {e}", exc_info=True)
        return df


# =============================================================================
# CORREGGI GEOMETRIE INVALIDE
# Ripara geometrie con auto-intersezioni o altri problemi topologici.
# =============================================================================

def op_geo_make_valid(
    df: DataFrame,
    geom_column: str
) -> gpd.GeoDataFrame:
    """
    Corregge geometrie invalide.

    Usa shapely.validation.make_valid se disponibile (shapely >= 1.8),
    altrimenti fallback con buffer(0).

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente

    Returns:
        GeoDataFrame con geometrie valide
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result = gdf.copy()

        invalid_count = (~result.geometry.is_valid).sum()

        if invalid_count == 0:
            logger.info("Make Valid: tutte le geometrie sono già valide")
            return result

        if HAS_MAKE_VALID:
            if HAS_SHAPELY2:
                result['geometry'] = shapely.make_valid(result.geometry)
            else:
                result['geometry'] = result.geometry.apply(
                    lambda geom: _make_valid(geom) if geom is not None and not geom.is_valid else geom
                )
        else:
            # Fallback: buffer(0) corregge molti problemi topologici
            logger.info("shapely.validation.make_valid non disponibile, uso buffer(0)")
            result['geometry'] = result.geometry.apply(
                lambda geom: geom.buffer(0) if geom is not None and not geom.is_valid else geom
            )

        fixed_count = invalid_count - (~result.geometry.is_valid).sum()
        logger.info(f"Make Valid completato: {fixed_count}/{invalid_count} geometrie corrette")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Make Valid: {e}", exc_info=True)
        return df


# =============================================================================
# FILTRO WITHIN (CONTENUTO IN)
# Filtra geometrie contenute interamente in un altro layer.
# =============================================================================

def op_geo_within(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str
) -> gpd.GeoDataFrame:
    """
    Filtra geometrie contenute interamente in un altro layer.

    Esegue un spatial join con predicato "within" e restituisce solo
    le colonne del layer sinistro, eliminando i duplicati.

    Args:
        dfs: Lista [layer_da_filtrare, layer_contenitore]
        left_geom_column: Colonna geometria del layer da filtrare
        right_geom_column: Colonna geometria del layer contenitore

    Returns:
        GeoDataFrame con sole righe del layer sinistro contenute nel destro
    """
    if len(dfs) < 2:
        logger.warning("Within richiede 2 input")
        return dfs[0] if dfs else gpd.GeoDataFrame()

    try:
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else to_geodataframe(dfs[1], right_geom_column)

        # Allinea CRS
        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        # Spatial join con predicato within
        joined = gpd.sjoin(left_gdf, right_gdf, how="inner", predicate="within")

        # Tieni solo le colonne del layer sinistro
        left_columns = list(left_gdf.columns)
        keep_cols = [c for c in left_columns if c in joined.columns]
        result = joined[keep_cols].copy()

        # Rimuovi duplicati (una geometria può essere within più poligoni sovrapposti)
        result = result.drop_duplicates().reset_index(drop=True)

        # Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Within completato: {len(left_gdf)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Within: {e}", exc_info=True)
        return dfs[0] if dfs else gpd.GeoDataFrame()


# =============================================================================
# PUNTO SULLA SUPERFICIE
# Punto garantito all'interno del poligono (a differenza del centroide).
# =============================================================================

def op_geo_point_on_surface(
    df: DataFrame,
    geom_column: str,
    output_column: str = "point_on_surface"
) -> gpd.GeoDataFrame:
    """
    Calcola un punto garantito all'interno di ogni poligono.

    A differenza del centroide, il punto rappresentativo è sempre
    all'interno della geometria, anche per forme concave o con buchi.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output

    Returns:
        GeoDataFrame con colonna punto sulla superficie
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        result = gdf.copy()
        result[output_column] = gdf.geometry.representative_point()

        logger.info(f"Point on Surface completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Point on Surface: {e}", exc_info=True)
        return df


# =============================================================================
# BORDO (BOUNDARY)
# Estrae il bordo delle geometrie poligonali come linee.
# =============================================================================

def op_geo_boundary(
    df: DataFrame,
    geom_column: str,
    output_column: str = "boundary"
) -> gpd.GeoDataFrame:
    """
    Estrae il bordo (boundary) delle geometrie.

    Per i poligoni restituisce il contorno come LineString/MultiLineString.
    Per le linee restituisce i punti estremi.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output

    Returns:
        GeoDataFrame con colonna boundary
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        result = gdf.copy()
        result[output_column] = gdf.geometry.boundary

        logger.info(f"Boundary completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Boundary: {e}", exc_info=True)
        return df


# =============================================================================
# DIAGRAMMA DI VORONOI
# Genera poligoni di Voronoi a partire da geometrie puntuali.
# =============================================================================

def op_geo_voronoi(
    df: DataFrame,
    geom_column: str
) -> gpd.GeoDataFrame:
    """
    Genera diagramma di Voronoi da geometrie puntuali.

    Ogni punto in input produce un poligono di Voronoi che rappresenta
    l'area più vicina a quel punto rispetto a tutti gli altri.
    Richiede shapely.ops.voronoi_diagram (shapely >= 1.8).

    Args:
        df: DataFrame input (deve contenere punti)
        geom_column: Colonna geometria sorgente

    Returns:
        GeoDataFrame con un poligono di Voronoi per ogni punto input
    """
    try:
        from shapely.geometry import MultiPoint
        from shapely.ops import unary_union

        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)

        if not HAS_VORONOI:
            logger.error("shapely.ops.voronoi_diagram non disponibile. Aggiornare shapely >= 1.8")
            return df

        # Crea MultiPoint da tutte le geometrie
        points = gdf.geometry.tolist()
        multipoint = MultiPoint(points)

        # Genera diagramma di Voronoi
        voronoi_geom = _voronoi_diagram(multipoint)

        # Estrai i poligoni individuali
        voronoi_polygons = list(voronoi_geom.geoms)

        # Associa ogni poligono di Voronoi al punto originale più vicino
        from shapely.geometry import Point
        result_rows = []
        for idx, row in gdf.iterrows():
            pt = row.geometry
            # Trova il poligono di Voronoi che contiene il punto
            matched_poly = None
            for poly in voronoi_polygons:
                if poly.contains(pt) or poly.touches(pt):
                    matched_poly = poly
                    break
            # Fallback: prendi il poligono più vicino
            if matched_poly is None:
                distances = [poly.distance(pt) for poly in voronoi_polygons]
                matched_poly = voronoi_polygons[distances.index(min(distances))]
            result_rows.append(matched_poly)

        result = gdf.copy()
        result['geometry'] = result_rows

        logger.info(f"Voronoi completato: {len(result)} poligoni generati")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Voronoi: {e}", exc_info=True)
        return df


# =============================================================================
# ESPORTA GEOMETRIA COME WKT / LAT-LON
# Aggiunge colonne testuali WKT e/o coordinate lat/lon.
# =============================================================================

def op_geo_to_wkt(
    df: DataFrame,
    geom_column: str,
    mode: str = "wkt",
    wkt_column: str = "geometry_wkt",
    lat_column: str = "latitude",
    lon_column: str = "longitude"
) -> gpd.GeoDataFrame:
    """
    Esporta geometria come WKT e/o coordinate lat/lon.

    Modalità disponibili:
    - "wkt": aggiunge colonna con rappresentazione WKT
    - "latlon": aggiunge colonne latitudine e longitudine (dal centroide)
    - "both": aggiunge sia WKT che lat/lon

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        mode: Modalità export ("wkt", "latlon", "both")
        wkt_column: Nome colonna WKT output
        lat_column: Nome colonna latitudine output
        lon_column: Nome colonna longitudine output

    Returns:
        GeoDataFrame con colonne aggiuntive
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result = gdf.copy()

        if mode in ("wkt", "both"):
            result[wkt_column] = result.geometry.apply(
                lambda geom: geom.wkt if geom is not None else None
            )

        if mode in ("latlon", "both"):
            centroids = result.geometry.centroid
            result[lon_column] = centroids.x
            result[lat_column] = centroids.y

        logger.info(f"To WKT completato: mode={mode}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore To WKT: {e}", exc_info=True)
        return df


# =============================================================================
# DIFFERENZA SIMMETRICA
# Operazione insiemistica: aree appartenenti a un solo layer (non comuni).
# =============================================================================

def op_geo_symmetric_difference(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str
) -> gpd.GeoDataFrame:
    """
    Differenza simmetrica tra due layer.

    Restituisce le aree che appartengono a un solo layer ma non ad entrambi
    (A XOR B). Equivalente a: (A - B) UNION (B - A).

    Args:
        dfs: Lista [layer_A, layer_B]
        left_geom_column: Colonna geometria di A
        right_geom_column: Colonna geometria di B

    Returns:
        GeoDataFrame con differenza simmetrica
    """
    if len(dfs) < 2:
        return dfs[0] if dfs else gpd.GeoDataFrame()

    try:
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else to_geodataframe(dfs[1], right_geom_column)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='symmetric_difference', keep_geom_type=False)

        # Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Symmetric Difference completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Symmetric Difference: {e}", exc_info=True)
        return dfs[0] if dfs else gpd.GeoDataFrame()


# =============================================================================
# COUNT POINTS IN POLYGONS — PointOnAreaOverlayer
# Conta/aggrega punti che ricadono dentro ciascun poligono.
# =============================================================================

def op_geo_count_points_in_polygons(
    dfs: List[DataFrame],
    polygon_geom_column: str,
    point_geom_column: str,
    count_column: str = "point_count",
    aggregate_column: str = None,
    aggregate_func: str = "sum"
) -> gpd.GeoDataFrame:
    """
    Conta i punti che ricadono in ciascun poligono e, opzionalmente,
    aggrega un attributo dei punti.

    Equivalente FME: PointOnAreaOverlayer.

    Args:
        dfs: Lista [poligoni, punti]
        polygon_geom_column: Colonna geometria dei poligoni
        point_geom_column: Colonna geometria dei punti
        count_column: Nome colonna conteggio output
        aggregate_column: Colonna dei punti da aggregare (opzionale)
        aggregate_func: Funzione di aggregazione: sum, mean, min, max

    Returns:
        GeoDataFrame dei poligoni con colonna conteggio e aggregato opzionale
    """
    if len(dfs) < 2:
        logger.warning("CountPointsInPolygons richiede 2 input")
        return dfs[0] if dfs else gpd.GeoDataFrame()

    try:
        poly_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else to_geodataframe(dfs[0], polygon_geom_column)
        point_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else to_geodataframe(dfs[1], point_geom_column)

        if len(poly_gdf) == 0 or len(point_gdf) == 0:
            logger.warning(f"CountPointsInPolygons: input vuoto (poly={len(poly_gdf)}, point={len(point_gdf)})")
            result = poly_gdf.copy()
            result[count_column] = 0
            return result

        # Allinea CRS
        poly_gdf, point_gdf = ensure_same_crs(poly_gdf, point_gdf)

        # Salva indice originale dei poligoni
        poly_idx_col = "__poly_idx__"
        poly_gdf = poly_gdf.copy()
        poly_gdf[poly_idx_col] = range(len(poly_gdf))

        # Spatial join: punti dentro poligoni
        joined = gpd.sjoin(
            point_gdf, poly_gdf,
            how="inner",
            predicate="within"
        )

        # Conteggio punti per poligono
        counts = joined.groupby("index_right").size().rename(count_column)

        # Aggregazione opzionale
        agg_series = None
        if aggregate_column and aggregate_column in joined.columns:
            valid_funcs = {"sum": "sum", "mean": "mean", "min": "min", "max": "max"}
            func = valid_funcs.get(aggregate_func, "sum")
            agg_col_name = f"{aggregate_column}_{func}"
            agg_series = (
                joined.groupby("index_right")[aggregate_column]
                .agg(func)
                .rename(agg_col_name)
            )

        # Unisci al GeoDataFrame dei poligoni
        result = poly_gdf.copy()
        result = result.join(counts, how="left")
        result[count_column] = result[count_column].fillna(0).astype(int)

        if agg_series is not None:
            result = result.join(agg_series, how="left")

        # Pulizia colonna indice temporanea
        result = result.drop(columns=[poly_idx_col], errors="ignore")

        logger.info(
            f"CountPointsInPolygons completato: {int(result[count_column].sum())} "
            f"punti in {len(result)} poligoni"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore CountPointsInPolygons: {e}", exc_info=True)
        return dfs[0] if dfs else gpd.GeoDataFrame()


# =============================================================================
# CLEAN TOPOLOGY — AreaGapAndOverlapCleaner
# Corregge gap e sovrapposizioni tra aree adiacenti.
# =============================================================================

def op_geo_clean_topology(
    df: DataFrame,
    geom_column: str,
    snap_tolerance: float = 0.001,
    remove_overlaps: bool = True,
    fill_gaps: bool = True
) -> gpd.GeoDataFrame:
    """
    Corregge problemi topologici: geometrie non valide, micro-gap
    e sovrapposizioni tra poligoni adiacenti.

    Equivalente FME: AreaGapAndOverlapCleaner.

    Strategia pragmatica:
    1. make_valid su tutte le geometrie
    2. buffer(+tol)/buffer(-tol) per chiudere micro-gap
    3. Rimozione sovrapposizioni tramite overlay iterativo

    Args:
        df: DataFrame/GeoDataFrame con geometrie poligonali
        geom_column: Colonna geometria
        snap_tolerance: Tolleranza di snapping in unità CRS
        remove_overlaps: Se True, rimuove sovrapposizioni
        fill_gaps: Se True, chiude micro-gap tra poligoni

    Returns:
        GeoDataFrame con topologia corretta
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result = gdf.copy()
        fixed_count = 0

        # Step 1: make_valid su tutte le geometrie
        if HAS_MAKE_VALID:
            for idx in result.index:
                geom = result.at[idx, result.geometry.name]
                if geom is not None and not geom.is_valid:
                    result.at[idx, result.geometry.name] = _make_valid(geom)
                    fixed_count += 1
        else:
            for idx in result.index:
                geom = result.at[idx, result.geometry.name]
                if geom is not None and not geom.is_valid:
                    result.at[idx, result.geometry.name] = geom.buffer(0)
                    fixed_count += 1

        # Step 2: chiudi micro-gap con buffer positivo/negativo
        if fill_gaps and snap_tolerance > 0:
            result[result.geometry.name] = (
                result.geometry
                .buffer(snap_tolerance)
                .buffer(-snap_tolerance)
            )

        # Step 3: rimuovi sovrapposizioni
        if remove_overlaps and len(result) > 1:
            from shapely.ops import unary_union
            geom_col_name = result.geometry.name
            geometries = list(result.geometry)
            # Itera in ordine: i poligoni precedenti hanno priorità
            for i in range(1, len(geometries)):
                if geometries[i] is None or geometries[i].is_empty:
                    continue
                # Unione di tutti i poligoni precedenti
                preceding = unary_union([g for g in geometries[:i] if g is not None and not g.is_empty])
                diff = geometries[i].difference(preceding)
                if not diff.is_empty:
                    geometries[i] = diff
                    fixed_count += 1
            result[geom_col_name] = geometries

        # Rimuovi geometrie vuote
        result = result[~result.geometry.is_empty].copy()

        logger.info(
            f"CleanTopology completato: {fixed_count} geometrie corrette, "
            f"{len(result)} righe in output"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore CleanTopology: {e}", exc_info=True)
        return df


# =============================================================================
# OVERLAY — AreaOnAreaOverlayer
# Overlay tra due layer poligonali con condivisione attributi.
# =============================================================================

def op_geo_overlay(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str,
    how: str = "intersection",
    keep_geom_type: bool = True
) -> gpd.GeoDataFrame:
    """
    Overlay tra due layer poligonali con condivisione attributi
    da entrambi i layer.

    Equivalente FME: AreaOnAreaOverlayer.

    A differenza di geo_intersection, supporta più modalità di overlay
    e mantiene gli attributi di entrambi i layer nel risultato.

    Args:
        dfs: Lista [layer_sinistro, layer_destro]
        left_geom_column: Colonna geometria del layer sinistro
        right_geom_column: Colonna geometria del layer destro
        how: Tipo di overlay: intersection, union, identity, symmetric_difference
        keep_geom_type: Se True, mantiene solo il tipo geometria dominante

    Returns:
        GeoDataFrame con geometrie risultanti e attributi di entrambi i layer
    """
    if len(dfs) < 2:
        logger.warning("Overlay richiede 2 input")
        return dfs[0] if dfs else gpd.GeoDataFrame()

    valid_how = ("intersection", "union", "identity", "symmetric_difference")
    if how not in valid_how:
        logger.warning(f"Overlay: modalità '{how}' non valida, uso 'intersection'")
        how = "intersection"

    try:
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else to_geodataframe(dfs[1], right_geom_column)

        # Allinea CRS
        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(
            left_gdf, right_gdf,
            how=how,
            keep_geom_type=keep_geom_type
        )

        # Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Overlay ({how}) completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Overlay: {e}", exc_info=True)
        return dfs[0] if dfs else gpd.GeoDataFrame()


# =============================================================================
# GEO BOUNDS EXTRACTOR — Estrai bounding box come attributi
# Calcola le coordinate min/max del rettangolo contenitore di ogni geometria.
# =============================================================================

def op_geo_bounds_extractor(
    df: DataFrame,
    geom_column: str,
    prefix: str = ""
) -> gpd.GeoDataFrame:
    """
    Estrae le coordinate del bounding box di ogni geometria come attributi.

    Per ogni riga aggiunge 4 colonne: min_x, min_y, max_x, max_y
    (con prefisso opzionale).

    Args:
        df: DataFrame o GeoDataFrame input
        geom_column: Colonna geometria
        prefix: Prefisso per i nomi delle colonne di output (es. 'bbox_')

    Returns:
        GeoDataFrame con colonne bounding box aggiunte
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result = gdf.copy()

        bounds = result.geometry.bounds  # DataFrame con minx, miny, maxx, maxy

        result[f"{prefix}min_x"] = bounds["minx"]
        result[f"{prefix}min_y"] = bounds["miny"]
        result[f"{prefix}max_x"] = bounds["maxx"]
        result[f"{prefix}max_y"] = bounds["maxy"]

        result = _ensure_active_geometry(result)

        logger.info(
            f"GeoBoundsExtractor completato: {len(result)} geometrie, "
            f"prefisso='{prefix}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore GeoBoundsExtractor: {e}", exc_info=True)
        return to_geodataframe(df, geom_column) if not isinstance(df, gpd.GeoDataFrame) else df


# =============================================================================
# GEO VERTEX COUNT — Conta i vertici di ogni geometria
# Utile per analisi della complessità geometrica.
# =============================================================================

def op_geo_vertex_count(
    df: DataFrame,
    geom_column: str,
    output_column: str = "vertex_count"
) -> gpd.GeoDataFrame:
    """
    Conta il numero di vertici per ogni geometria.

    Args:
        df: DataFrame o GeoDataFrame input
        geom_column: Colonna geometria
        output_column: Nome della colonna di output (default: 'vertex_count')

    Returns:
        GeoDataFrame con colonna conteggio vertici aggiunta
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result = gdf.copy()

        if HAS_SHAPELY2:
            result[output_column] = shapely.get_num_coordinates(result.geometry)
        else:
            def _count_coords(geom):
                """Conta i vertici di una geometria di qualsiasi tipo."""
                if geom is None or geom.is_empty:
                    return 0
                if hasattr(geom, "exterior"):
                    return len(geom.exterior.coords)
                elif hasattr(geom, "coords"):
                    return len(geom.coords)
                elif hasattr(geom, "geoms"):
                    return sum(_count_coords(part) for part in geom.geoms)
                return 0

            result[output_column] = result.geometry.apply(_count_coords)

        result = _ensure_active_geometry(result)

        logger.info(
            f"GeoVertexCount completato: {len(result)} geometrie, "
            f"output='{output_column}'"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore GeoVertexCount: {e}", exc_info=True)
        return to_geodataframe(df, geom_column) if not isinstance(df, gpd.GeoDataFrame) else df


# =============================================================================
# GEO LINE BUILDER — Crea linee da punti ordinati raggruppati
# Collega punti in LineString per gruppo, con ordine opzionale.
# =============================================================================

def op_geo_line_builder(
    df: DataFrame,
    geom_column: str,
    group_column: str,
    order_column: str = None,
    output_column: str = "geometry"
) -> gpd.GeoDataFrame:
    """
    Crea LineString da punti ordinati raggruppati per attributo.

    Raggruppa i punti per group_column, opzionalmente li ordina per
    order_column, e crea una LineString per ogni gruppo.

    Args:
        df: DataFrame o GeoDataFrame con geometrie punto
        geom_column: Colonna geometria (punti)
        group_column: Colonna di raggruppamento
        order_column: Colonna di ordinamento all'interno del gruppo (opzionale)
        output_column: Nome della colonna geometria di output (default: 'geometry')

    Returns:
        GeoDataFrame con una LineString per ogni gruppo
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result_rows = []

        for group_val, group_df in gdf.groupby(group_column):
            if order_column and order_column in group_df.columns:
                group_df = group_df.sort_values(by=order_column)

            coords = [(geom.x, geom.y) for geom in group_df.geometry if geom is not None and not geom.is_empty]

            if len(coords) >= 2:
                line = LineString(coords)
                result_rows.append({group_column: group_val, output_column: line})
            else:
                logger.warning(
                    f"GeoLineBuilder: gruppo '{group_val}' ha meno di 2 punti, ignorato"
                )

        if not result_rows:
            logger.warning("GeoLineBuilder: nessuna linea creata")
            return gpd.GeoDataFrame(columns=[group_column, output_column])

        result = gpd.GeoDataFrame(result_rows, geometry=output_column, crs=gdf.crs)

        logger.info(
            f"GeoLineBuilder completato: {len(gdf)} punti -> {len(result)} linee"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore GeoLineBuilder: {e}", exc_info=True)
        return to_geodataframe(df, geom_column) if not isinstance(df, gpd.GeoDataFrame) else df


# =============================================================================
# GEO POLYGON BUILDER — Crea poligoni da punti ordinati raggruppati
# Collega punti in Polygon per gruppo, chiudendo automaticamente l'anello.
# =============================================================================

def op_geo_polygon_builder(
    df: DataFrame,
    geom_column: str,
    group_column: str,
    order_column: str = None,
    output_column: str = "geometry"
) -> gpd.GeoDataFrame:
    """
    Crea Polygon da punti ordinati raggruppati per attributo.

    Raggruppa i punti per group_column, opzionalmente li ordina per
    order_column, e crea un Polygon per ogni gruppo. L'anello viene
    chiuso automaticamente (primo punto = ultimo punto).

    Args:
        df: DataFrame o GeoDataFrame con geometrie punto
        geom_column: Colonna geometria (punti)
        group_column: Colonna di raggruppamento
        order_column: Colonna di ordinamento all'interno del gruppo (opzionale)
        output_column: Nome della colonna geometria di output (default: 'geometry')

    Returns:
        GeoDataFrame con un Polygon per ogni gruppo
    """
    try:
        gdf = df if isinstance(df, gpd.GeoDataFrame) else to_geodataframe(df, geom_column)
        result_rows = []

        for group_val, group_df in gdf.groupby(group_column):
            if order_column and order_column in group_df.columns:
                group_df = group_df.sort_values(by=order_column)

            coords = [(geom.x, geom.y) for geom in group_df.geometry if geom is not None and not geom.is_empty]

            if len(coords) >= 3:
                # Chiudi l'anello se non è già chiuso
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                polygon = Polygon(coords)
                result_rows.append({group_column: group_val, output_column: polygon})
            else:
                logger.warning(
                    f"GeoPolygonBuilder: gruppo '{group_val}' ha meno di 3 punti, ignorato"
                )

        if not result_rows:
            logger.warning("GeoPolygonBuilder: nessun poligono creato")
            return gpd.GeoDataFrame(columns=[group_column, output_column])

        result = gpd.GeoDataFrame(result_rows, geometry=output_column, crs=gdf.crs)

        logger.info(
            f"GeoPolygonBuilder completato: {len(gdf)} punti -> {len(result)} poligoni"
        )
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore GeoPolygonBuilder: {e}", exc_info=True)
        return to_geodataframe(df, geom_column) if not isinstance(df, gpd.GeoDataFrame) else df