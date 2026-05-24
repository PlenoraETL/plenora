"""
Operazioni di trasformazione geospaziali per MANIPOLA.

Keeps transformation behavior isolated from orchestration callers.
"""

from typing import List, Optional

from app.utils.logger import get_logger
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.geodata import (
    active_geometry_value_at,
    empty_geodataframe,
    ensure_same_crs,
    geodataframe_drop_duplicates_reset_index,
    geodataframe_from_geometry_series,
    geodataframe_sjoin,
    geodataframe_sjoin_nearest,
    is_geodataframe,
    set_active_geometry_value_at,
    to_geodataframe,
)
from app.core.manipola.transforms.spatial_helpers import (
    _buffer_per_row_local_utm,
    _ensure_active_geometry,
    _resolve_metric_crs,
    _resolve_output_geom_crs,
)
from app.core.manipola.transforms.spatial_construction import (
    op_geo_line_builder,
    op_geo_polygon_builder,
)
from app.core.manipola.transforms.spatial_overlay import (
    op_geo_clip,
    op_geo_count_points_in_polygons,
    op_geo_difference,
    op_geo_intersection,
    op_geo_overlay,
    op_geo_symmetric_difference,
    op_geo_union,
    op_sjoin,
)

_SPATIAL_OVERLAY_COMPAT_EXPORTS = (
    op_geo_line_builder,
    op_geo_polygon_builder,
    op_geo_clip,
    op_geo_count_points_in_polygons,
    op_geo_difference,
    op_geo_intersection,
    op_geo_overlay,
    op_geo_symmetric_difference,
    op_geo_union,
    op_sjoin,
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

logger = get_logger(__name__)

DataFrame = FrameLike


# BUFFER
# Crea area di rispetto attorno alle geometrie. Distanza in metri, riproiezione automatica

def op_geo_buffer(
    df: DataFrame,
    geom_column: str,
    distance: float,
    output_column: str = "buffer_geom",
    cap_style: str = "round",
    metric_crs: Optional[str] = None,
    output_crs: str = "same",
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
        original_crs = gdf.crs

        # Auto-UTM globale per tutto il GDF distorce le geometrie lontane
        # dalla zona scelta sul centroide (es. punti italiani + Tokyo: la
        # zona scelta sul centroide globale rende il buffer ~0.7 km invece
        # di 1 km su Tokyo). Quando l'utente non specifica un metric_crs
        # esplicito e l'input e' geografico con bbox > 6 gradi di longitudine,
        # passiamo a buffer per-riga in UTM locale.
        # `metric_crs` arriva come "" dal registry quando l'utente non lo
        # imposta esplicitamente (default JSON e' stringa vuota, non null);
        # bisogna trattarlo come "non specificato".
        no_explicit_metric_crs = not (
            isinstance(metric_crs, str) and metric_crs.strip()
        )
        use_per_row_utm = (
            no_explicit_metric_crs
            and gdf.crs is not None
            and gdf.crs.is_geographic
            and not gdf.empty
            and (gdf.total_bounds[2] - gdf.total_bounds[0]) > 6
        )

        if use_per_row_utm:
            buffer_geom = _buffer_per_row_local_utm(
                gdf.geometry, distance, cap_style_int
            )
            metric_used = None  # gia' riproiettato a original_crs per riga
            current_geom_crs = original_crs
        else:
            metric_used = _resolve_metric_crs(gdf, metric_crs)
            if metric_used is not None:
                gdf_projected = gdf.to_crs(metric_used)
            else:
                gdf_projected = gdf
            buffer_geom = gdf_projected.geometry.buffer(distance, cap_style=cap_style_int)
            current_geom_crs = metric_used if metric_used is not None else original_crs

        target_crs = _resolve_output_geom_crs(original_crs, metric_used, output_crs)
        if target_crs is not None and str(target_crs) != str(current_geom_crs):
            buffer_gdf = geodataframe_from_geometry_series(buffer_geom, crs=current_geom_crs)
            buffer_geom = buffer_gdf.to_crs(target_crs).geometry

        result = gdf.copy()
        result[output_column] = buffer_geom.values

        mode = "per-row UTM" if use_per_row_utm else f"single-CRS {metric_used or original_crs}"
        logger.info(f"Buffer completato: {distance}m, output={output_column}, mode={mode}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Buffer: {e}", exc_info=True)
        raise


# CENTROIDE
# Calcola il punto centrale di ogni geometria

def op_geo_centroid(
    df: DataFrame,
    geom_column: str,
    output_column: str = "centroid",
    metric_crs: Optional[str] = None,
    output_crs: str = "same",
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
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
            centroid_gdf = geodataframe_from_geometry_series(centroids, crs=current_geom_crs)
            centroids = centroid_gdf.to_crs(target_crs).geometry

        result = gdf.copy()
        result[output_column] = centroids.values

        logger.info(f"Centroid completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Centroid: {e}", exc_info=True)
        raise


# AREA
# Calcola area in mÂ². Riproietta automaticamente in CRS metrico se necessario

def op_geo_area(
    df: DataFrame,
    geom_column: str,
    output_column: str = "area_m2",
    metric_crs: Optional[str] = None,
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

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
        raise


# DISSOLVE (FUSIONE
# Fonde geometrie raggruppate per attributo con funzione di aggregazione

def op_geo_dissolve(
    df: DataFrame,
    geom_column: str,
    by_column: str = None,
    aggfunc: str = "first"
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

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
        raise


# SEMPLIFICAZIONE
# Riduce complessitÃ  geometrie mantenendo la forma (algoritmo Douglas-Peucker

def op_geo_simplify(
    df: DataFrame,
    geom_column: str,
    tolerance: float = 0.001,
    preserve_topology: bool = True
) -> DataFrame:
    """
    Semplifica geometrie (Douglas-Peucker).
    
    Args:
        df: DataFrame input
        geom_column: Colonna geometria
        tolerance: Tolleranza semplificazione (unitÃ  del CRS)
        preserve_topology: Se True, evita auto-intersezioni
        
    Returns:
        GeoDataFrame con geometrie semplificate
    """
    if tolerance is None or tolerance <= 0:
        raise ValueError(f"Simplify tolerance deve essere > 0, ricevuto: {tolerance}")

    try:
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

        geom_col_name = gdf.geometry.name
        gdf[geom_col_name] = gdf.geometry.simplify(tolerance, preserve_topology=preserve_topology)

        logger.info(f"Simplify completato: tolerance={tolerance}")
        return gdf
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Simplify: {e}", exc_info=True)
        raise


# INVILUPPO CONVESSO
# Calcola il poligono convesso minimo che contiene ogni geometria

def op_geo_convex_hull(
    df: DataFrame,
    geom_column: str,
    output_column: str = "convex_hull"
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

        convex_hulls = gdf.geometry.convex_hull
        
        result = gdf.copy()
        result[output_column] = convex_hulls.values
        
        logger.info(f"Convex Hull completato: output={output_column}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Convex Hull: {e}", exc_info=True)
        raise


# RIPROIEZIONE CRS
# Cambia sistema di coordinate (es: WGS84 â†’ UTM 32N, EPSG:4326 â†’ EPSG:32632

def op_geo_reproject(
    df: DataFrame,
    geom_column: str,
    target_crs: str = "EPSG:4326",
    source_crs: str = None
) -> DataFrame:
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
        # B2 NIENTE fallback silente a EPSG:4326 quando source_crs
        # manca. Un dataset metrico (es. EPSG:32632) trattato come
        # geografico viene "riproiettato" producendo coordinate plausibili
        # ma traslate di centinaia di km: il bug resta silenzioso finche'
        # qualcuno nota le geometrie nel posto sbagliato. Fail-loud sotto
        # se nessun CRS e' recuperabile
        crs_hint = source_crs  # puo' essere None: nessun default
        gdf = to_geodataframe(df, geom_column, crs=crs_hint)

        # Se source_crs Ã¨ fornito esplicitamente, forzalo (override auto-detect
        if source_crs and gdf.crs and str(gdf.crs) != source_crs:
            logger.info(f"Override CRS sorgente: {gdf.crs} -> {source_crs}")
            gdf = gdf.set_crs(source_crs, allow_override=True)

        if gdf.crs is None and source_crs:
            gdf = gdf.set_crs(source_crs)

        if gdf.crs is None:
            raise ValueError(
                "op_geo_reproject: source_crs non fornito e nessun CRS "
                "rilevabile dalle geometrie (no EWKB SRID, no CRS sul "
                f"GeoDataFrame). Specificare 'source_crs' esplicito "
                f"(es. 'EPSG:4326', 'EPSG:32632') per riproiettare verso "
                f"'{target_crs}'."
            )

        original_crs = gdf.crs
        result = gdf.to_crs(target_crs)

        logger.info(f"Reproject completato: {original_crs} -> {target_crs}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Reproject: {e}", exc_info=True)
        raise

# BOUNDING BOX (INVILUPPO RETTANGOLARE
# Calcola il rettangolo minimo che contiene ogni geometria

def op_geo_envelope(
    df: DataFrame,
    geom_column: str,
    output_column: str = "envelope"
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

        envelopes = gdf.geometry.envelope
        
        result = gdf.copy()
        result[output_column] = envelopes.values
        
        logger.info(f"Envelope completato: output={output_column}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore Envelope: {e}", exc_info=True)
        raise


# DISTANZA TRA GEOMETRIE
# Calcola la distanza tra geometrie di due layer in metri

def op_geo_distance(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str,
    output_column: str = "distance_m",
    metric_crs: Optional[str] = None,
) -> DataFrame:
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
        return dfs[0] if dfs else empty_geodataframe([])

    try:
        left_gdf = dfs[0] if is_geodataframe(dfs[0]) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if is_geodataframe(dfs[1]) else to_geodataframe(dfs[1], right_geom_column)

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
        raise


# NEAREST NEIGHBOR JOIN
# Join per vicinanza: associa ad ogni geometria il vicino piÃ¹ prossimo

def op_geo_nearest(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str,
    max_distance: Optional[float] = None,
    output_column: str = "distance_m",
    metric_crs: Optional[str] = None,
    output_crs: str = "same",
) -> DataFrame:
    """
    Nearest neighbor join tra due layer.

    Per ogni geometria del layer sinistro, trova il vicino piÃ¹ prossimo
    nel layer destro e ne riporta gli attributi con la distanza.

    Args:
        dfs: Lista [layer_sinistro, layer_destro]
        left_geom_column: Colonna geometria del layer sinistro
        right_geom_column: Colonna geometria del layer destro
        max_distance: Distanza massima in unitÃ  del CRS metrico scelto.
        output_column: Nome colonna distanza output
        metric_crs: CRS per calcolo distanza. None -> auto-UTM se geografico.
        output_crs: "same" | "metric" | "EPSG:xxxx"

    Returns:
        GeoDataFrame con attributi del vicino piÃ¹ prossimo e distanza
    """
    if len(dfs) < 2:
        logger.warning("Nearest richiede 2 input")
        return dfs[0] if dfs else empty_geodataframe([])

    try:
        left_gdf = dfs[0] if is_geodataframe(dfs[0]) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if is_geodataframe(dfs[1]) else to_geodataframe(dfs[1], right_geom_column)

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
        result = geodataframe_sjoin_nearest(
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
        raise


# LUNGHEZZA LINEE
# Calcola la lunghezza in metri di geometrie lineari

def op_geo_length(
    df: DataFrame,
    geom_column: str,
    output_column: str = "length_m",
    metric_crs: Optional[str] = None,
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

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
        raise


# PERIMETRO POLIGONI
# Calcola il perimetro in metri dei poligoni

def op_geo_perimeter(
    df: DataFrame,
    geom_column: str,
    output_column: str = "perimeter_m",
    metric_crs: Optional[str] = None,
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

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
        raise


# ESPLODI MULTI-GEOMETRIE
# Converte MultiPolygon/MultiLineString/MultiPoint in geometrie singole

def op_geo_explode(
    df: DataFrame,
    geom_column: str
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

        result = gdf.explode(index_parts=False).reset_index(drop=True)

        logger.info(f"Explode completato: {len(gdf)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Explode: {e}", exc_info=True)
        raise


# CORREGGI GEOMETRIE INVALIDE
# Ripara geometrie con auto-intersezioni o altri problemi topologici

def op_geo_make_valid(
    df: DataFrame,
    geom_column: str
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
        result = gdf.copy()

        invalid_count = (~result.geometry.is_valid).sum()

        if invalid_count == 0:
            logger.info("Make Valid: tutte le geometrie sono giÃ  valide")
            return result

        if HAS_MAKE_VALID:
            geom_col_name = result.geometry.name
            if HAS_SHAPELY2:
                result[geom_col_name] = shapely.make_valid(result.geometry)
            else:
                result[geom_col_name] = result.geometry.apply(
                    lambda geom: _make_valid(geom) if geom is not None and not geom.is_valid else geom
                )
        else:
            # Fallback: buffer(0) corregge molti problemi topologici
            logger.info("shapely.validation.make_valid non disponibile, uso buffer(0)")
            geom_col_name = result.geometry.name
            result[geom_col_name] = result.geometry.apply(
                lambda geom: geom.buffer(0) if geom is not None and not geom.is_valid else geom
            )

        fixed_count = invalid_count - (~result.geometry.is_valid).sum()
        logger.info(f"Make Valid completato: {fixed_count}/{invalid_count} geometrie corrette")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Make Valid: {e}", exc_info=True)
        raise


# FILTRO WITHIN (CONTENUTO IN
# Filtra geometrie contenute interamente in un altro layer

def op_geo_within(
    dfs: List[DataFrame],
    left_geom_column: str,
    right_geom_column: str
) -> DataFrame:
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
        return dfs[0] if dfs else empty_geodataframe([])

    try:
        left_gdf = dfs[0] if is_geodataframe(dfs[0]) else to_geodataframe(dfs[0], left_geom_column)
        right_gdf = dfs[1] if is_geodataframe(dfs[1]) else to_geodataframe(dfs[1], right_geom_column)

        # Allinea CRS
        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        # Spatial join con predicato within
        joined = geodataframe_sjoin(
            left_gdf,
            right_gdf,
            how="inner",
            predicate="within",
        )

        # Tieni solo le colonne del layer sinistro
        left_columns = list(left_gdf.columns)
        keep_cols = [c for c in left_columns if c in joined.columns]
        result = joined[keep_cols].copy()

        # Rimuovi duplicati (una geometria puÃ² essere within piÃ¹ poligoni sovrapposti
        result = geodataframe_drop_duplicates_reset_index(result)

        # Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Within completato: {len(left_gdf)} -> {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Within: {e}", exc_info=True)
        raise


# PUNTO SULLA SUPERFICIE
# Punto garantito all'interno del poligono (a differenza del centroide

def op_geo_point_on_surface(
    df: DataFrame,
    geom_column: str,
    output_column: str = "point_on_surface"
) -> DataFrame:
    """
    Calcola un punto garantito all'interno di ogni poligono.

    A differenza del centroide, il punto rappresentativo Ã¨ sempre
    all'interno della geometria, anche per forme concave o con buchi.

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        output_column: Nome colonna output

    Returns:
        GeoDataFrame con colonna punto sulla superficie
    """
    try:
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

        result = gdf.copy()
        result[output_column] = gdf.geometry.representative_point()

        logger.info(f"Point on Surface completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Point on Surface: {e}", exc_info=True)
        raise


# BORDO (BOUNDARY
# Estrae il bordo delle geometrie poligonali come linee

def op_geo_boundary(
    df: DataFrame,
    geom_column: str,
    output_column: str = "boundary"
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

        result = gdf.copy()
        result[output_column] = gdf.geometry.boundary

        logger.info(f"Boundary completato: output={output_column}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Boundary: {e}", exc_info=True)
        raise


# DIAGRAMMA DI VORONOI
# Genera poligoni di Voronoi a partire da geometrie puntuali

def op_geo_voronoi(
    df: DataFrame,
    geom_column: str
) -> DataFrame:
    """
    Genera diagramma di Voronoi da geometrie puntuali.

    Ogni punto in input produce un poligono di Voronoi che rappresenta
    l'area piÃ¹ vicina a quel punto rispetto a tutti gli altri.
    Richiede shapely.ops.voronoi_diagram (shapely >= 1.8).

    Args:
        df: DataFrame input (deve contenere punti)
        geom_column: Colonna geometria sorgente

    Returns:
        GeoDataFrame con un poligono di Voronoi per ogni punto input
    """
    try:
        from shapely.geometry import MultiPoint
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)

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

        # Associa ogni punto alla cella Voronoi che lo contiene via STRtree
        # O(log n) per query vs O(n) lineare). Il legacy usava first-match
        # per contains|touches nell'ordine della lista; qui usiamo
        # `predicate="intersects"` (superset di contains+touches) e
        # prendiamo l'indice minimo fra i candidati per preservare l'ordine
        from shapely.strtree import STRtree
        tree = STRtree(voronoi_polygons)
        pts = gdf.geometry.to_numpy()
        result_rows: List = []
        for pt in pts:
            candidates = tree.query(pt, predicate="intersects")
            if len(candidates) > 0:
                matched_poly = voronoi_polygons[int(candidates.min())]
            else:
                # Fallback: poligono piu' vicino (caso degenere; con
                # voronoi_diagram shapely questo non dovrebbe mai servire
                nearest_idx = tree.nearest(pt)
                matched_poly = voronoi_polygons[int(nearest_idx)]
            result_rows.append(matched_poly)

        result = gdf.copy()
        result[result.geometry.name] = result_rows

        logger.info(f"Voronoi completato: {len(result)} poligoni generati")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Voronoi: {e}", exc_info=True)
        raise


# ESPORTA GEOMETRIA COME WKT / LAT-LON
# Aggiunge colonne testuali WKT e/o coordinate lat/lon

def op_geo_to_wkt(
    df: DataFrame,
    geom_column: str,
    mode: str = "wkt",
    wkt_column: str = "geometry_wkt",
    lat_column: str = "latitude",
    lon_column: str = "longitude"
) -> DataFrame:
    """
    Esporta geometria come WKT e/o coordinate lat/lon.

    ModalitÃ  disponibili:
    - "wkt": aggiunge colonna con rappresentazione WKT
    - "latlon": aggiunge colonne latitudine e longitudine (dal centroide)
    - "both": aggiunge sia WKT che lat/lon

    Args:
        df: DataFrame input
        geom_column: Colonna geometria sorgente
        mode: ModalitÃ  export ("wkt", "latlon", "both")
        wkt_column: Nome colonna WKT output
        lat_column: Nome colonna latitudine output
        lon_column: Nome colonna longitudine output

    Returns:
        GeoDataFrame con colonne aggiuntive
    """
    try:
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
        result = gdf.copy()

        if mode in ("wkt", "both"):
            # Pre-fix per-row apply su GeoSeries (fino a 100k geometrie
            # in pipeline reali). Post-fix vettoriale via
            # `geometry_series_to_wkt`: ~2.4x sui punti, ~1.3-1.7x sui
            # poligoni; contratto uniforme su missing (None/NaN/pd.NA)
            # → None.
            from app.core.dataframes.geometry_serialization import (
                geometry_series_to_wkt,
            )
            result[wkt_column] = geometry_series_to_wkt(result.geometry)

        if mode in ("latlon", "both"):
            centroids = result.geometry.centroid
            result[lon_column] = centroids.x
            result[lat_column] = centroids.y

        logger.info(f"To WKT completato: mode={mode}")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore To WKT: {e}", exc_info=True)
        raise

# CLEAN TOPOLOGY â€” AreaGapAndOverlapCleaner
# Corregge gap e sovrapposizioni tra aree adiacenti

def op_geo_clean_topology(
    df: DataFrame,
    geom_column: str,
    snap_tolerance: float = 0.001,
    remove_overlaps: bool = True,
    fill_gaps: bool = True
) -> DataFrame:
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
        snap_tolerance: Tolleranza di snapping in unitÃ  CRS
        remove_overlaps: Se True, rimuove sovrapposizioni
        fill_gaps: Se True, chiude micro-gap tra poligoni

    Returns:
        GeoDataFrame con topologia corretta
    """
    try:
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
        result = gdf.copy()
        fixed_count = 0

        # Step 1: make_valid su tutte le geometrie
        if HAS_MAKE_VALID:
            for idx in result.index:
                geom = active_geometry_value_at(result, idx)
                if geom is not None and not geom.is_valid:
                    set_active_geometry_value_at(result, idx, _make_valid(geom))
                    fixed_count += 1
        else:
            for idx in result.index:
                geom = active_geometry_value_at(result, idx)
                if geom is not None and not geom.is_valid:
                    set_active_geometry_value_at(result, idx, geom.buffer(0))
                    fixed_count += 1

        # Step 2: chiudi micro-gap con buffer positivo/negativo
        if fill_gaps and snap_tolerance > 0:
            result[result.geometry.name] = (
                result.geometry
                .buffer(snap_tolerance)
                .buffer(-snap_tolerance)
            )

        # Step 3: rimuovi sovrapposizioni
        # NOTE: per ogni i, ricostruisce `unary_union(geometries[:i])`
        # da zero â€” O(n^2). Qui teniamo un accumulator incrementale
        # al passo i, `accumulated` e' gia' l'unione dei geometries[0..i-1
        # processati. Complessita' effettiva ~O(n) union pairwise
        # Nota FP: pair-union vs cascading-union possono differire a
        # livello di coordinate; i test perf_correctness usano tolerance
        # topologica (equals â†’ equals_exact â†’ area-relative 1e-6
        if remove_overlaps and len(result) > 1:
            from shapely.ops import unary_union
            geom_col_name = result.geometry.name
            geometries = list(result.geometry)

            accumulated = None  # lazy: union di tutti i geometries[0..i-1] validi
            for i in range(1, len(geometries)):
                g = geometries[i]
                if g is None or g.is_empty:
                    continue
                if accumulated is None:
                    # Primo passo con geometries[i] valido: lazy-init da [0..i-1
                    preceding_list = [
                        ga for ga in geometries[:i]
                        if ga is not None and not ga.is_empty
                    ]
                    if preceding_list:
                        accumulated = unary_union(preceding_list)
                    else:
                        # Nessun preceding non-empty: legacy avrebbe fatto
                        # diff = g.difference(empty_collection) = g
                        # non-empty â†’ fixed_count++ e geometries[i] = g
                        geometries[i] = g
                        fixed_count += 1
                        accumulated = g
                        continue
                diff = g.difference(accumulated)
                if not diff.is_empty:
                    geometries[i] = diff
                    fixed_count += 1
                    accumulated = accumulated.union(diff)
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
        raise


# GEO BOUNDS EXTRACTOR â€” Estrai bounding box come attributi
# Calcola le coordinate min/max del rettangolo contenitore di ogni geometria

def op_geo_bounds_extractor(
    df: DataFrame,
    geom_column: str,
    prefix: str = ""
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
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
        raise


# GEO VERTEX COUNT â€” Conta i vertici di ogni geometria
# Utile per analisi della complessitÃ  geometrica

def op_geo_vertex_count(
    df: DataFrame,
    geom_column: str,
    output_column: str = "vertex_count"
) -> DataFrame:
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
        gdf = df if is_geodataframe(df) else to_geodataframe(df, geom_column)
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
        raise
