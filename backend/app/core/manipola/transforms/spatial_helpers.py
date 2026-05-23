"""
Helper condivisi per le trasformazioni geospaziali MANIPOLA.

Il modulo resta privato al cluster spatial: non registra transform e non cambia
la superficie pubblica di `spatial.py`.
"""

from collections import defaultdict
from typing import Optional

from app.core.dataframes.geodata import geoseries_from_geometry_values
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _ensure_active_geometry(gdf):
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


def get_utm_crs_for_geometry(gdf) -> str:
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
    gdf, metric_crs: Optional[str]
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


def _utm_epsg_for_lonlat(lon: float, lat: float) -> str:
    """UTM zone EPSG per un singolo punto (geog WGS84)."""
    zone = int((lon + 180) / 6) + 1
    if zone < 1:
        zone = 1
    elif zone > 60:
        zone = 60
    return f"EPSG:326{zone:02d}" if lat >= 0 else f"EPSG:327{zone:02d}"


def _buffer_per_row_local_utm(
    geoseries, distance: float, cap_style_int: int
):
    """Buffer in UTM locale, riproiettato all'origine.

    Sceglie la zona UTM sul centroide di ogni geometria, ma raggruppa le
    geometrie per EPSG per evitare un to_crs/buffer/to_crs per singola riga.
    """
    from shapely.geometry import Point

    src_crs = geoseries.crs or "EPSG:4326"
    results: list[Optional[object]] = [None] * len(geoseries)
    groups = defaultdict(list)

    for pos, geom in enumerate(geoseries):
        if geom is None or geom.is_empty:
            continue
        try:
            centroid: Point = geom.centroid
            lon, lat = float(centroid.x), float(centroid.y)
            if abs(lat) >= 89.0 or abs(lon) >= 179.5:
                logger.warning(
                    "Buffer per-riga: punto a singolarita' (lon=%.4f, lat=%.4f) "
                    "saltato (UTM degenera al polo/antimeridiano).",
                    lon, lat,
                )
                continue
            groups[_utm_epsg_for_lonlat(lon, lat)].append((pos, geom))
        except Exception as e:
            logger.warning(f"Buffer per-riga fallito su geometria: {e}")

    for utm, items in groups.items():
        try:
            positions = [pos for pos, _ in items]
            geoms = [geom for _, geom in items]

            projected = geoseries_from_geometry_values(geoms, crs=src_crs).to_crs(utm)
            buffered = projected.buffer(distance, cap_style=cap_style_int)
            back = buffered.to_crs(src_crs)

            for pos, geom in zip(positions, back.values):
                results[pos] = geom
        except Exception as e:
            logger.warning(
                "Buffer batch UTM fallito per %s (%d geometrie), fallback per-riga: %s",
                utm,
                len(items),
                e,
            )
            for pos, geom in items:
                try:
                    row = geoseries_from_geometry_values([geom], crs=src_crs).to_crs(utm)
                    buffered = row.buffer(distance, cap_style=cap_style_int)
                    back = buffered.to_crs(src_crs)
                    results[pos] = back.iloc[0]
                except Exception as row_error:
                    logger.warning(f"Buffer per-riga fallito su geometria: {row_error}")

    return geoseries_from_geometry_values(results, crs=src_crs, index=geoseries.index)


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
