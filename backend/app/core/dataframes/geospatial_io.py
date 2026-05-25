"""Geospatial DataFrame export helpers owned by the dataframe boundary.

Post-Phase5 refactor: solo l'entry-point bytes-first
`geospatial_dataframe_to_bytes` sopravvive. Le funzioni path-based
(save_geospatial_dataframe, _salva_formato_geo, _salva_shapefile_zippato,
_salva_geo_vuoto, _switch_colonna_geometria) sono state rimosse —
nessun caller live in app/. In produzione il flow e' bytes -> storage.
"""

from typing import Optional

import pandas as pd

from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.geodata import detect_geometry_column, to_geodataframe
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _geo_helpers():
    return to_geodataframe, detect_geometry_column


def _compact_whole_number_float_properties(gdf):
    """Per le colonne NON-geometry, converte float-tutti-interi in Int64.

    Stessa logica di `dataframe_io._compact_whole_number_floats` ma
    applicata al GeoDataFrame, saltando la colonna di geometria attiva
    (e qualunque altra colonna di tipo geometry secondaria). Evita
    output GeoJSON con properties ``"age": 41.0`` quando il valore
    originale era un intero promosso a float per la presenza di NaN.
    """
    if gdf is None or gdf.empty:
        return gdf
    try:
        active_geom_name = gdf.geometry.name
    except Exception:
        active_geom_name = None
    out = gdf
    for col in list(gdf.columns):
        if col == active_geom_name:
            continue
        try:
            if hasattr(gdf[col], "geom_type"):
                continue
        except Exception:
            pass
        s = gdf[col]
        if pd.api.types.is_float_dtype(s):
            non_nan = s.dropna()
            if not non_nan.empty and (non_nan % 1 == 0).all():
                if out is gdf:
                    out = copy_frame_for_update(gdf)
                try:
                    out[col] = out[col].astype("Int64")
                except (TypeError, ValueError):
                    continue
    return out


def _dedup_columns_for_geo_save(df: pd.DataFrame, formato: str) -> pd.DataFrame:
    """Rename duplicated columns before geospatial export."""
    if not df.columns.duplicated(keep="first").any():
        return df
    new_columns: list[str] = []
    seen: set[str] = set()
    for col in df.columns:
        if col in seen:
            idx = 2
            while f"{col}_{idx}" in seen:
                idx += 1
            new_name = f"{col}_{idx}"
        else:
            new_name = col
        new_columns.append(new_name)
        seen.add(new_name)
    df = copy_frame_for_update(df)
    df.columns = new_columns
    logger.warning(
        f"Save {formato}: colonne duplicate rinominate "
        f"posizionalmente per evitare crash geopandas."
    )
    return df


def geospatial_dataframe_to_bytes(
    df: pd.DataFrame,
    formato: str,
    geom_column: Optional[str],
    crs: str,
) -> bytes:
    """Serialize a DataFrame to bytes for supported geospatial formats.

    Formati supportati (tutti RAM-only dal caller's perspective):
      - `geojson`: single-file, serializzato via `gdf.to_json`.
      - `gpkg` / `geopackage`: single-file SQLite via pyogrio bytes.
      - `shp`: multi-file zippato. Il boundary `io.geospatial.
        write_shapefile_zip_bytes` usa una temp dir INTERNA al
        modulo per scrivere i sidecar e li zippa; il caller riceve
        solo lo ZIP di bytes.
    """
    if formato not in ("geojson", "gpkg", "geopackage", "shp"):
        raise ValueError(f"Formato '{formato}' non supportato")

    to_geodataframe, detect_geometry_column = _geo_helpers()
    df = _dedup_columns_for_geo_save(df, formato)
    if geom_column is None:
        geom_column = detect_geometry_column(df)
    try:
        import geopandas as gpd
    except ImportError:
        raise ValueError("geopandas non installato: formato geospatial non disponibile")
    if not isinstance(df, gpd.GeoDataFrame):
        if not geom_column:
            raise ValueError(f"{formato}: serve una colonna geometria")
        gdf = to_geodataframe(df, geom_column, crs)
    else:
        gdf = copy_frame_for_update(df)
        if geom_column and gdf.geometry.name != geom_column:
            gdf = gdf.set_geometry(geom_column)
    gdf = _converti_geometrie_secondarie_in_wkt(gdf)
    gdf = _compact_whole_number_float_properties(gdf)

    if formato == "geojson":
        return gdf.to_json().encode("utf-8")
    if formato == "shp":
        from app.core.io.geospatial import write_shapefile_zip_bytes
        return write_shapefile_zip_bytes(gdf, name="layer")
    # GPKG/GeoPackage: write via pyogrio bytes (RAM-only).
    # NB: `layer=None` -> pyogrio usa "default" come layer name.
    # Passare "gpkg" come layer name e' vietato da OGR (prefisso
    # riservato GeoPackage).
    from app.core.io.geospatial import write_vector_bytes
    return write_vector_bytes(gdf, driver="GPKG")


_GEOMETRY_LIKE_COLUMN_NAMES = {
    "geom", "geometry", "shape", "the_geom",
    "geom_4326", "geom_wgs84",
}


def _converti_geometrie_secondarie_in_wkt(gdf):
    """Convert and rename non-active geometry-like columns for OGR export."""
    from shapely.geometry.base import BaseGeometry

    nome_geometria_attiva = gdf.geometry.name
    renames: dict[str, str] = {}

    for col in list(gdf.columns):
        if col == nome_geometria_attiva:
            continue

        col_data = gdf[col].dropna()
        if not col_data.empty:
            primo_valido = col_data.iloc[0]
            if isinstance(primo_valido, BaseGeometry):
                # Pre-fix: `apply(lambda x: x.wkt if BaseGeometry
                # else x)` per-riga. Post-fix: serializzazione
                # vettoriale via `geometry_series_to_wkt` con
                # `preserve_non_geometry=True` per mantenere il
                # comportamento del lambda originale (caso edge:
                # serie geometry-like che contiene mix Shapely +
                # altro). Centralizza l'ultimo path WKT residuo
                # rimasto fuori dalla single source of truth.
                from app.core.dataframes.geometry_serialization import (
                    geometry_series_to_wkt,
                )
                gdf[col] = geometry_series_to_wkt(
                    col_data, preserve_non_geometry=True
                )

        # Rinomina se il nome collide con i nomi-tipo di geometry,
        # cosi' il driver OGR non prova ad aggiungere un secondo
        # geometry field nello schema del layer.
        if col.lower() in _GEOMETRY_LIKE_COLUMN_NAMES:
            new_name = f"{col}_wkt"
            base_new = new_name
            n = 2
            while new_name in gdf.columns or new_name in renames.values():
                new_name = f"{base_new}_{n}"
                n += 1
            renames[col] = new_name

    if renames:
        gdf = gdf.rename(columns=renames)
        logger.warning(
            "Rinominate colonne geometry-like non-attive per "
            "evitare conflitti col driver OGR: "
            + ", ".join(f"{o}->{n}" for o, n in renames.items())
        )

    return gdf


