"""
Geometry construction transforms for MANIPOLA spatial operations.
"""

from typing import Optional, Union

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon

from app.utils.geo import to_geodataframe
from app.utils.logger import get_logger

logger = get_logger(__name__)

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


def op_geo_line_builder(
    df: DataFrame,
    geom_column: str,
    group_column: str,
    order_column: Optional[str] = None,
    output_column: str = "geometry",
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

            coords = [
                (geom.x, geom.y)
                for geom in group_df.geometry
                if geom is not None and not geom.is_empty
            ]

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
        raise


def op_geo_polygon_builder(
    df: DataFrame,
    geom_column: str,
    group_column: str,
    order_column: Optional[str] = None,
    output_column: str = "geometry",
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

            coords = [
                (geom.x, geom.y)
                for geom in group_df.geometry
                if geom is not None and not geom.is_empty
            ]

            if len(coords) >= 3:
                # Chiudi l'anello se non e' gia' chiuso.
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
        raise
