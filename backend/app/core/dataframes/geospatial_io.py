"""Geospatial DataFrame export helpers owned by the dataframe boundary.

Solo l'entry-point bytes-first `geospatial_dataframe_to_bytes` resta attivo.
Le funzioni path-based
(save_geospatial_dataframe, _salva_formato_geo, _salva_shapefile_zippato,
_salva_geo_vuoto, _switch_colonna_geometria) sono state rimosse —
nessun caller live in app/. In produzione il flow e' bytes -> storage.
"""

import re
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Optional

import pandas as pd

from app.core.dataframes import (
    geometry_serialization as geometry_serialization_module,
)
from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.geodata import detect_geometry_column, to_geodataframe
from app.core.io import geospatial as geospatial_io_module
from app.utils.logger import get_logger

logger = get_logger(__name__)


_GEOMETRY_LIKE_COLUMN_NAMES = {
    "geom", "geometry", "shape", "the_geom",
    "geom_4326", "geom_wgs84",
}
_TEMPORAL_LOGICAL_TYPES = {"date", "datetime", "timestamp"}


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


def _unique_shapefile_field_name(name: str, used: set[str]) -> str:
    """Return a DBF-compatible field name matching GDAL's 10-char limit."""
    base = str(name or "")[:10] or "field"
    candidate = base
    counter = 1
    while candidate.lower() in used:
        suffix = f"_{counter}"
        candidate = f"{base[: max(1, 10 - len(suffix))]}{suffix}"
        counter += 1
    used.add(candidate.lower())
    return candidate


def exported_column_names_for_format(
    columns,
    formato: str,
    *,
    geom_column: Optional[str] = None,
) -> list[str]:
    """Return physical column names expected after export.

    Shapefile stores attributes in DBF, whose field names are capped at 10
    characters. GDAL/pyogrio launders longer fields during write; downstream
    project-source contracts must expose those physical names or consumers
    fail when validating the materialized artifact.
    """
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in columns:
        name = str(raw_name)
        if name in seen:
            counter = 2
            candidate = f"{name}_{counter}"
            while candidate in seen:
                counter += 1
                candidate = f"{name}_{counter}"
            name = candidate
        names.append(name)
        seen.add(name)

    # Pyogrio espone sempre la geometria attiva come ``geometry`` al
    # round-trip. Le geometrie secondarie vengono convertite e rinominate in
    # WKT da ``_converti_geometrie_secondarie_in_wkt``.
    physical_names: list[str] = []
    secondary_renames: set[str] = set()
    for name in names:
        if geom_column and name == geom_column:
            physical_names.append("geometry")
            continue
        if name.lower() in _GEOMETRY_LIKE_COLUMN_NAMES:
            candidate = f"{name}_wkt"
            base_candidate = candidate
            counter = 2
            while candidate in names or candidate in secondary_renames:
                candidate = f"{base_candidate}_{counter}"
                counter += 1
            secondary_renames.add(candidate)
            physical_names.append(candidate)
            continue
        physical_names.append(name)

    if (formato or "").lower() != "shp":
        return physical_names

    used: set[str] = set()
    out: list[str] = []
    for name in physical_names:
        if name == "geometry":
            out.append(name)
            continue
        out.append(_unique_shapefile_field_name(name, used))
    return out


def _is_temporal_logical_type(raw_type: Any) -> bool:
    logical = str(raw_type or "").strip().lower()
    return logical in _TEMPORAL_LOGICAL_TYPES or logical.startswith("datetime64")


def _parse_declared_temporal_text(value: str) -> datetime:
    text = value.strip()
    iso_year_first = re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", text)
    parsed = pd.to_datetime(
        text,
        format="mixed",
        dayfirst=not bool(iso_year_first),
        errors="raise",
    )
    return parsed.to_pydatetime() if isinstance(parsed, pd.Timestamp) else parsed


def _normalize_geo_temporal_scalar(
    value: Any,
    *,
    declared_type: Any,
) -> tuple[Any, bool]:
    if value is None or value is pd.NA or value is pd.NaT:
        return None, True
    try:
        if pd.isna(value):
            return None, True
    except (TypeError, ValueError):
        pass

    parsed = value
    if isinstance(parsed, str):
        if not _is_temporal_logical_type(declared_type):
            return value, False
        if not parsed.strip():
            return None, True
        try:
            parsed = _parse_declared_temporal_text(parsed)
        except (TypeError, ValueError, OverflowError):
            return value, False
    elif isinstance(parsed, pd.Timestamp):
        parsed = parsed.to_pydatetime()
    elif isinstance(parsed, date) and not isinstance(parsed, datetime):
        parsed = datetime.combine(parsed, time.min)

    if not isinstance(parsed, datetime):
        return value, False
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if str(declared_type or "").strip().lower() == "date":
        parsed = datetime.combine(parsed.date(), time.min)
    return parsed, True


def _prepare_temporal_columns_for_geo(
    gdf,
    formato: str,
    column_types: Optional[Mapping[str, Any]],
):
    """Normalize temporal attributes for the target OGR driver."""
    declared_exact = dict(column_types or {})
    declared_lower = {
        str(name).lower(): raw_type
        for name, raw_type in declared_exact.items()
    }
    active_geometry = gdf.geometry.name
    out = gdf

    for column in gdf.columns:
        if column == active_geometry:
            continue
        series = gdf[column]
        declared_type = declared_exact.get(str(column)) or declared_lower.get(
            str(column).lower()
        )
        dtype_temporal = pd.api.types.is_datetime64_any_dtype(series.dtype)
        sample = series.dropna().head(20)
        native_temporal = any(isinstance(value, (date, datetime, pd.Timestamp)) for value in sample)
        if not (
            dtype_temporal
            or native_temporal
            or _is_temporal_logical_type(declared_type)
        ):
            continue

        normalized: list[Any] = []
        invalid_values: list[Any] = []
        for value in series:
            converted, valid = _normalize_geo_temporal_scalar(
                value,
                declared_type=declared_type,
            )
            normalized.append(converted)
            if not valid:
                invalid_values.append(value)

        if out is gdf:
            out = copy_frame_for_update(gdf)

        if formato == "shp":
            # DBF non supporta DateTime. ISO conserva data, ora e valori
            # anteriori al 1900 senza conversioni implicite del driver.
            non_null_values = series.dropna().tolist()
            native_date_only = bool(non_null_values) and all(
                isinstance(value, date) and not isinstance(value, datetime)
                for value in non_null_values
            )
            date_only = (
                str(declared_type or "").strip().lower() == "date"
                or native_date_only
            )
            out[column] = [
                (
                    value.date().isoformat()
                    if date_only and isinstance(value, datetime)
                    else value.isoformat(sep=" ")
                    if isinstance(value, datetime)
                    else value
                )
                for value in normalized
            ]
            continue

        if invalid_values:
            raise ValueError(
                f"{formato}: colonna temporale dichiarata {column!r} contiene "
                f"valori non validi (esempio: {invalid_values[0]!r})"
            )
        out[column] = pd.to_datetime(
            pd.Series(normalized, index=series.index),
            errors="raise",
        )
    return out


def geospatial_dataframe_to_bytes(
    df: pd.DataFrame,
    formato: str,
    geom_column: Optional[str],
    crs: str,
    *,
    column_types: Optional[Mapping[str, Any]] = None,
) -> bytes:
    """Serialize a DataFrame to bytes for supported geospatial formats.

    Formati supportati (tutti bytes-only dal caller's perspective):
      - `geojson`: single-file, serializzato dal driver OGR GeoJSON.
      - `gpkg` / `geopackage`: single-file SQLite via pyogrio bytes.
      - `shp`: multi-file zippato. Il boundary `io.geospatial.
        write_shapefile_zip_bytes` usa `/vsimem` oppure, su Windows,
        una temp dir interna per i sidecar; il caller riceve solo lo ZIP.
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
    gdf = _prepare_temporal_columns_for_geo(gdf, formato, column_types)

    if formato == "geojson":
        return geospatial_io_module.write_vector_bytes(gdf, driver="GeoJSON")
    if formato == "shp":
        return geospatial_io_module.write_shapefile_zip_bytes(gdf, name="layer")
    # GPKG/GeoPackage: write via pyogrio bytes (RAM-only).
    # NB: `layer=None` -> pyogrio usa "default" come layer name.
    # Passare "gpkg" come layer name e' vietato da OGR (prefisso
    # riservato GeoPackage).
    return geospatial_io_module.write_vector_bytes(gdf, driver="GPKG")

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
                # Serializzazione vettoriale via `geometry_series_to_wkt` con
                # `preserve_non_geometry=True` per mantenere il
                # comportamento del lambda originale (caso edge:
                # serie geometry-like che contiene mix Shapely +
                # altro). Centralizza l'ultimo path WKT residuo
                # rimasto fuori dalla single source of truth.
                gdf[col] = geometry_serialization_module.geometry_series_to_wkt(
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
