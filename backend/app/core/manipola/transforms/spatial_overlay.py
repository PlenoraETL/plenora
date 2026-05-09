"""
Overlay, clip and spatial join transforms for MANIPOLA.

This module is private to the spatial transform cluster; public imports stay
anchored in `spatial.py`.
"""

import sys
from typing import List, Union

import geopandas as gpd
import pandas as pd

from app.core.manipola.transforms.spatial_helpers import _ensure_active_geometry
from app.utils.geo import ensure_same_crs, to_geodataframe as _to_geodataframe
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


def _to_gdf(df: DataFrame, geom_column: str) -> gpd.GeoDataFrame:
    spatial_module = sys.modules.get("app.core.manipola.transforms.spatial")
    converter = getattr(spatial_module, "to_geodataframe", _to_geodataframe)
    return converter(df, geom_column)


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
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else _to_gdf(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else _to_gdf(inputs[1], right_geom)

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
        raise


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
        input_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else _to_gdf(inputs[0], input_geom)
        mask_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else _to_gdf(inputs[1], mask_geom)

        # Allinea CRS
        input_gdf, mask_gdf = ensure_same_crs(input_gdf, mask_gdf)

        result = gpd.clip(input_gdf, mask_gdf)

        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Clip completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Clip: {e}", exc_info=True)
        raise


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
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else _to_gdf(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else _to_gdf(inputs[1], right_geom)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='intersection', keep_geom_type=False)

        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Intersection completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Intersection: {e}", exc_info=True)
        raise


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
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else _to_gdf(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else _to_gdf(inputs[1], right_geom)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='union', keep_geom_type=False)

        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Union geometrica completata: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Union geometrica: {e}", exc_info=True)
        raise


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
        left_gdf = inputs[0] if isinstance(inputs[0], gpd.GeoDataFrame) else _to_gdf(inputs[0], left_geom)
        right_gdf = inputs[1] if isinstance(inputs[1], gpd.GeoDataFrame) else _to_gdf(inputs[1], right_geom)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='difference', keep_geom_type=False)

        # FIX: Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Difference completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Difference: {e}", exc_info=True)
        raise


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
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else _to_gdf(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else _to_gdf(dfs[1], right_geom_column)

        left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)

        result = gpd.overlay(left_gdf, right_gdf, how='symmetric_difference', keep_geom_type=False)

        # Assicura geometria attiva valida
        result = _ensure_active_geometry(result)

        logger.info(f"Symmetric Difference completato: {len(result)} righe")
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore Symmetric Difference: {e}", exc_info=True)
        raise


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
        poly_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else _to_gdf(dfs[0], polygon_geom_column)
        point_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else _to_gdf(dfs[1], point_geom_column)

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
        raise


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

    A differenza di geo_intersection, supporta piu' modalita' di overlay
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
        logger.warning(f"Overlay: modalita' '{how}' non valida, uso 'intersection'")
        how = "intersection"

    try:
        left_gdf = dfs[0] if isinstance(dfs[0], gpd.GeoDataFrame) else _to_gdf(dfs[0], left_geom_column)
        right_gdf = dfs[1] if isinstance(dfs[1], gpd.GeoDataFrame) else _to_gdf(dfs[1], right_geom_column)

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
        raise
