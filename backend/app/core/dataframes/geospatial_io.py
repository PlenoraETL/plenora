"""Geospatial DataFrame export helpers owned by the dataframe boundary."""

import json
import re
import tempfile
import zipfile
from pathlib import Path
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


def save_geospatial_dataframe(
    df: pd.DataFrame,
    base_path: Path,
    formato: str,
    geom_column: Optional[str],
    crs: str,
) -> Path:
    """Save a DataFrame in a geospatial filesystem format."""
    to_geodataframe, detect_geometry_column = _geo_helpers()
    df = _dedup_columns_for_geo_save(df, formato)
    return _salva_formato_geo(
        df,
        base_path,
        formato,
        geom_column,
        crs,
        to_geodataframe,
        detect_geometry_column,
    )


def geospatial_dataframe_to_bytes(
    df: pd.DataFrame,
    formato: str,
    geom_column: Optional[str],
    crs: str,
) -> bytes:
    """Serialize a DataFrame to bytes for supported geospatial formats."""
    if formato != "geojson":
        if formato in ("gpkg", "shp"):
            raise ValueError(
                f"Formato '{formato}' richiede filesystem (tool binari / multi-file). "
                f"Usa save_dataframe(path-based) invece di save_dataframe_to_bytes."
            )
        raise ValueError(f"Formato '{formato}' non supportato")

    to_geodataframe, detect_geometry_column = _geo_helpers()
    df = _dedup_columns_for_geo_save(df, formato)
    if geom_column is None:
        geom_column = detect_geometry_column(df)
    try:
        import geopandas as gpd
    except ImportError:
        raise ValueError("geopandas non installato: formato geojson non disponibile")
    if not isinstance(df, gpd.GeoDataFrame):
        if not geom_column:
            raise ValueError("geojson: serve una colonna geometria")
        gdf = to_geodataframe(df, geom_column, crs)
    else:
        gdf = copy_frame_for_update(df)
        if geom_column and gdf.geometry.name != geom_column:
            gdf = gdf.set_geometry(geom_column)
    gdf = _converti_geometrie_secondarie_in_wkt(gdf)
    gdf = _compact_whole_number_float_properties(gdf)
    return gdf.to_json().encode("utf-8")


def _sanitize_layer_name(name: str) -> str:
    """Sanitize an OGR/GPKG layer name."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "layer"


def _salva_formato_geo(
    df: pd.DataFrame,
    base_path: Path,
    formato: str,
    geom_column: Optional[str],
    crs: str,
    to_geodataframe,
    detect_geometry_column,
) -> Path:
    """Save a DataFrame as GeoJSON, GPKG, or zipped Shapefile."""
    import geopandas as gpd

    if len(df) == 0:
        logger.warning(f"DataFrame vuoto, creazione {formato} vuoto")
        return _salva_geo_vuoto(df, base_path, formato)

    if isinstance(df, gpd.GeoDataFrame):
        gdf = copy_frame_for_update(df)

        if geom_column and geom_column != gdf.geometry.name and geom_column in gdf.columns:
            gdf = _switch_colonna_geometria(gdf, geom_column)

        gdf = _converti_geometrie_secondarie_in_wkt(gdf)

        if gdf.crs is None:
            gdf = gdf.set_crs(crs)

    else:
        if geom_column is None:
            geom_column = detect_geometry_column(df)

        if geom_column is None:
            lat_col = next((c for c in df.columns if c.lower() in ("lat", "latitude", "lat_wgs84")), None)
            lon_col = next(
                (c for c in df.columns if c.lower() in ("lon", "lng", "longitude", "long", "lon_wgs84")),
                None,
            )
            if lat_col and lon_col:
                from shapely.geometry import Point

                df = copy_frame_for_update(df)
                lons = pd.to_numeric(df[lon_col], errors="coerce")
                lats = pd.to_numeric(df[lat_col], errors="coerce")
                valid = lons.notna() & lats.notna()
                df["geometry"] = [
                    Point(lon, lat) if v else None
                    for lon, lat, v in zip(lons, lats, valid)
                ]
                geom_column = "geometry"
                logger.info(
                    f"Auto-creata colonna geometry da lat='{lat_col}' lon='{lon_col}' "
                    f"per salvataggio {formato}"
                )
            else:
                raise ValueError(
                    f"Nessuna colonna geometria trovata per formato {formato}. "
                    f"Colonne disponibili: {list(df.columns)}. "
                    f"Usa CSV o JSON per dati non spaziali."
                )

        gdf = to_geodataframe(df, geom_column, crs=crs)
        gdf = _converti_geometrie_secondarie_in_wkt(gdf)

    gdf = _compact_whole_number_float_properties(gdf)

    if formato == "geojson":
        final_path = base_path.with_suffix(".geojson")
        gdf.to_file(final_path, driver="GeoJSON")
    elif formato == "gpkg":
        final_path = base_path.with_suffix(".gpkg")
        layer_name = _sanitize_layer_name(base_path.stem)
        gdf.to_file(final_path, driver="GPKG", layer=layer_name)
    elif formato == "shp":
        final_path = _salva_shapefile_zippato(gdf, base_path, df.columns)
    else:
        raise ValueError(f"Formato '{formato}' non supportato")

    return final_path


def _switch_colonna_geometria(gdf, geom_column: str):
    """Set a different column as active GeoDataFrame geometry."""
    from app.core.dataframes.geometry_parsing import parse_geometry_series

    col_data = gdf[geom_column]
    if col_data.dtype == "object":
        _non_null = col_data.dropna()
        primo_valido = _non_null.iloc[0] if not _non_null.empty else None
        if primo_valido and isinstance(primo_valido, str):
            gdf[geom_column] = parse_geometry_series(col_data)

    return gdf.set_geometry(geom_column)


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


def _salva_geo_vuoto(df: pd.DataFrame, base_path: Path, formato: str) -> Path:
    """Create an empty but structurally valid geospatial output."""
    import geopandas as gpd

    if formato == "geojson":
        final_path = base_path.with_suffix(".geojson")
        geojson_vuoto = {"type": "FeatureCollection", "features": []}
        with open(final_path, "w", encoding="utf-8") as f:
            json.dump(geojson_vuoto, f)
        return final_path

    gdf_vuoto = gpd.GeoDataFrame(
        {col: [] for col in df.columns},
        geometry=[],
        crs="EPSG:4326",
    )

    if formato == "gpkg":
        final_path = base_path.with_suffix(".gpkg")
        layer_name = _sanitize_layer_name(base_path.stem)
        gdf_vuoto.to_file(final_path, driver="GPKG", layer=layer_name)
    elif formato == "shp":
        final_path = _salva_shapefile_zippato(gdf_vuoto, base_path, df.columns)
    else:
        raise ValueError(f"Formato '{formato}' non supportato")

    return final_path


def _salva_shapefile_zippato(gdf, base_path: Path, colonne_originali) -> Path:
    """Save a GeoDataFrame as zipped Shapefile."""
    nome_shp = base_path.stem

    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = Path(tmpdir) / f"{nome_shp}.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile")

        zip_path = base_path.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(tmpdir).glob(f"{nome_shp}.*"):
                zf.write(f, f.name)

        colonne_lunghe = [c for c in colonne_originali if len(str(c)) > 10]
        if colonne_lunghe:
            logger.warning(
                f"Shapefile: {len(colonne_lunghe)} colonne con nomi > 10 caratteri "
                f"saranno troncate: {colonne_lunghe[:5]}..."
            )

    return zip_path
