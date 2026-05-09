"""
Helper condivisi per le trasformazioni geospaziali MANIPOLA.

Il modulo resta privato al cluster spatial: non registra transform e non cambia
la superficie pubblica di `spatial.py`.
"""

from typing import Optional

import geopandas as gpd

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _ensure_active_geometry(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Assicura che il GeoDataFrame abbia una geometria attiva valida.

    Dopo operazioni come sjoin, se entrambi i dataframe avevano una colonna
    'geometry', una puo' essere rinominata (es. 'geometry_right',
    'geometry_roma') e il GeoDataFrame perde il riferimento alla geometria
    attiva.

    Questa funzione trova la prima colonna geometry disponibile e la imposta
    come geometria attiva.
    """
    try:
        # Se la geometria attiva esiste nelle colonne, tutto ok
        if gdf.geometry.name in gdf.columns:
            return gdf
    except Exception as _active_geom_err:
        logger.debug(
            f"Probe geometria attiva fallito, provo discovery colonne: "
            f"{_active_geom_err}"
        )

    # Trova colonne di tipo geometry
    geom_cols = []
    for col in gdf.columns:
        try:
            if hasattr(gdf[col], "geom_type") or gdf[col].dtype.name == "geometry":
                geom_cols.append(col)
        except Exception as _col_probe_err:
            logger.debug(
                f"Probe colonna geometry '{col}' fallito: {_col_probe_err}"
            )
            continue

    if geom_cols:
        # Preferisci 'geometry' se esiste, altrimenti la prima trovata
        if "geometry" in geom_cols:
            gdf = gdf.set_geometry("geometry")
        else:
            gdf = gdf.set_geometry(geom_cols[0])
        logger.info(f"Geometria attiva impostata su: {gdf.geometry.name}")

    return gdf


def get_utm_crs_for_geometry(gdf: gpd.GeoDataFrame) -> str:
    """Calcola la zona UTM appropriata per un GeoDataFrame."""
    if gdf.empty:
        return "EPSG:32632"

    try:
        centroide = gdf.geometry.unary_union.centroid
        lon, lat = centroide.x, centroide.y

        bounds = gdf.total_bounds
        lon_range = bounds[2] - bounds[0]
        if lon_range > 6:
            logger.warning(
                "UTM zone guard CRITICAL: bbox copre %.2f gradi di longitudine "
                "(>6). La zona UTM scelta distorce le geometrie ai bordi. "
                "Considera CRS custom (es. Lambert conformico) per operazioni "
                "metriche accurate.",
                lon_range,
            )
        elif lon_range > 3:
            logger.warning(
                "UTM zone guard: bbox copre %.2f gradi di longitudine (>3). "
                "Accuratezza metrica marginale per geometrie ai bordi della zona.",
                lon_range,
            )

        zona_utm = int((lon + 180) / 6) + 1
        if lat >= 0:
            return f"EPSG:326{zona_utm:02d}"
        return f"EPSG:327{zona_utm:02d}"
    except Exception as e:
        logger.warning(
            "get_utm_crs_for_geometry: calcolo UTM fallito, uso default EPSG:32632: %s",
            e,
        )
        return "EPSG:32632"


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
