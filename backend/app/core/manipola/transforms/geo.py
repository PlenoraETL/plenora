"""
Cluster GEO — costruzione geometria da coordinate (lat/lon o WKT).

Keeps transformation behavior isolated from orchestration callers.
"""

import pandas as pd
import geopandas as gpd
from typing import Union

from app.utils.logger import get_logger

logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# GEOMETRIA DA COORDINATE
# Crea colonna geometry Point da colonne latitudine/longitudine.

def op_geo_from_coords(
    df: DataFrame,
    mode: str = "latlon",
    lat_column: str = "",
    lon_column: str = "",
    wkt_column: str = "",
    output_column: str = "geometry",
    crs: str = "EPSG:4326"
) -> DataFrame:
    """
    Crea colonna geometria da coordinate lat/lon o da WKT.

    Args:
        df: DataFrame input
        mode: 'latlon' per colonne lat/lon, 'wkt' per colonna WKT
        lat_column: Nome colonna latitudine
        lon_column: Nome colonna longitudine
        wkt_column: Nome colonna WKT
        output_column: Nome colonna geometria output
        crs: Sistema di riferimento (default EPSG:4326)

    Returns:
        GeoDataFrame con colonna geometria
    """
    from shapely.geometry import Point
    from shapely import wkt

    result = df.copy()

    try:
        if mode == 'latlon':
            if not lat_column or lat_column not in result.columns:
                logger.warning(f"Geo from Coords: colonna lat '{lat_column}' non trovata")
                return df
            if not lon_column or lon_column not in result.columns:
                logger.warning(f"Geo from Coords: colonna lon '{lon_column}' non trovata")
                return df

            # Converti a numerico
            lats = pd.to_numeric(result[lat_column], errors='coerce')
            lons = pd.to_numeric(result[lon_column], errors='coerce')

            # Crea Point (lon, lat) - ordine GIS standard
            geometries = []
            for lat, lon in zip(lats, lons):
                if pd.notna(lat) and pd.notna(lon):
                    geometries.append(Point(lon, lat))
                else:
                    geometries.append(None)

            result[output_column] = geometries

        elif mode == 'wkt':
            if not wkt_column or wkt_column not in result.columns:
                logger.warning(f"Geo from Coords: colonna WKT '{wkt_column}' non trovata")
                return df

            def safe_wkt_parse(val):
                """Parsa una stringa WKT in oggetto Shapely; restituisce None per valori vuoti o malformati."""
                if pd.isna(val) or not val or str(val).strip() == '':
                    return None
                try:
                    return wkt.loads(str(val))
                except Exception as _wkt_err:
                    # Parse WKT fallito per riga singola — debug (row-loop:
                    # NOTE: causerebbe flood su dataset con WKT malformati).
                    logger.debug(f"safe_wkt_parse fallita: {_wkt_err}")
                    return None

            result[output_column] = result[wkt_column].apply(safe_wkt_parse)

        else:
            logger.warning(f"Geo from Coords: modo '{mode}' non supportato")
            return df

        # Converti a GeoDataFrame
        gdf = gpd.GeoDataFrame(result, geometry=output_column, crs=crs)

        logger.info(f"Geo from Coords ({mode}): -> {output_column} [{crs}]")
        return gdf

    except Exception as e:
        logger.error(f"[CRASH] Errore Geo from Coords: {e}", exc_info=True)
        raise
