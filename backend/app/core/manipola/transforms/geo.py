"""
Cluster GEO — costruzione geometria da coordinate (lat/lon o WKT).

Origine
=======
Estratto da `app.core.manipola.transforms.base` come parte di Phase 5
Batch B1.11 (cluster split + closeout runtime residuals base.py, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B1 + §6.1 facade pattern).

Razionale del raggruppamento: singolo op self-contained che costruisce
geometria shapely/geopandas a partire da colonne sorgente. Nessun
helper privato, nessuna dipendenza con altri cluster di transforms.
Imports `Point` / `wkt` mantenuti come function-local (lazy) per
preservare bit-per-bit il pattern legacy.

Contenuto:
  - `op_geo_from_coords` — crea colonna `geometry` (Point) da
                           colonne lat/lon, oppure parsa WKT con
                           `safe_wkt_parse` (debug-log su parse fail
                           singola riga, no flood).

Invarianti pubbliche preservate (regola §6.1 zero call-site changes):
  - `from app.core.manipola.transforms.base import op_geo_from_coords`
    continua a funzionare grazie al facade re-export in `base.py`.
  - `from app.core.manipola.transforms import op_geo_from_coords`
    continua a funzionare (`__init__.py` re-exporta via
    `from .base import op_geo_from_coords` che cattura il facade).
  - `transforms_registry.json` con entry `"base.op_geo_from_coords"`
    continua a risolvere via `getattr(<base module>, "op_geo_from_coords")`
    dal `PluginExecutor`.
  - Nuovo path canonico `from app.core.manipola.transforms.geo
    import op_geo_from_coords` disponibile.

Logger naming
=============
Logger creato esplicitamente come
`"app.core.manipola.transforms.base"` (NON `__name__`) per preservare
il contratto osservabile pre-extraction: i record `Geo from Coords:
...`, `[CRASH] Errore Geo from Coords` continuano ad apparire sotto
il logger name dei consumer. Stesso pattern di tutti i moduli figli
estratti nei batch B1.1-B1.10.

Il body sotto e' una copia BIT-PER-BIT dell'implementazione in
`base.py` al momento di B1.11. Nessuna modifica di logica, comment,
log payload, ordine di operazione, fallback warning-on-missing-cols,
debug-on-bad-wkt, GeoDataFrame conversion in coda.
"""

import pandas as pd
import geopandas as gpd
from typing import Union

from app.utils.logger import get_logger

# Stesso logger name di `base.py`: i record sono indistinguibili dai
# consumer che attaccano handler / filtrano per
# "app.core.manipola.transforms.base" (pattern §6.1 facade).
logger = get_logger("app.core.manipola.transforms.base")

DataFrame = Union[pd.DataFrame, gpd.GeoDataFrame]


# =============================================================================
# GEOMETRIA DA COORDINATE
# Crea colonna geometry Point da colonne latitudine/longitudine.
# =============================================================================

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
                    # warning causerebbe flood su dataset con WKT malformati).
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
