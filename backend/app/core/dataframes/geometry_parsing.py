import json
from typing import Any, Optional

import pandas as pd
import shapely
from shapely import wkb, wkt
from shapely.geometry import shape

from app.utils.logger import get_logger

logger = get_logger(__name__)

GEOMETRY_COLUMN_NAMES = [
    "geometry", "geom", "the_geom", "shape", "wkb_geometry", "wkt"
]

WKT_PREFIXES = (
    "POINT", "LINESTRING", "POLYGON",
    "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON",
    "GEOMETRYCOLLECTION",
)


def looks_like_wkb_hex(value: str) -> bool:
    if len(value) < 20 or not value.startswith("0"):
        return False
    return all(c in "0123456789ABCDEFabcdef" for c in value[:20])


def parse_geometry(value: Any) -> Optional[Any]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if value == "" or value == "None":
        return None
    if hasattr(value, "geom_type"):
        return value

    value_str = str(value).strip()
    if not value_str:
        return None

    if value_str.startswith("{"):
        try:
            geojson = json.loads(value_str)
            if isinstance(geojson, dict) and "type" in geojson:
                geojson_type = geojson["type"]
                if geojson_type == "FeatureCollection":
                    features = geojson.get("features", [])
                    if features and features[0].get("geometry"):
                        return shape(features[0]["geometry"])
                    return None
                if geojson_type == "Feature":
                    geom = geojson.get("geometry")
                    return shape(geom) if geom else None
                return shape(geojson)
        except Exception as e:
            logger.debug("parse_geometry: GeoJSON parsing fallito per %r: %s", value_str[:80], e)

    value_upper = value_str.upper()
    if any(value_upper.startswith(prefix) for prefix in WKT_PREFIXES):
        try:
            return wkt.loads(value_str)
        except Exception as e:
            logger.debug("parse_geometry: WKT parsing fallito per %r: %s", value_str[:80], e)

    if looks_like_wkb_hex(value_str):
        geom = parse_wkb_hex(value_str)
        if geom is not None:
            return geom

    if isinstance(value, (bytes, memoryview)):
        try:
            return wkb.loads(bytes(value))
        except Exception as e:
            logger.debug("parse_geometry: WKB bytes parsing fallito: %s", e)

    try:
        return wkb.loads(value_str, hex=True)
    except Exception as e:
        logger.debug("parse_geometry: tutti i tentativi falliti per %r: %s", value_str[:80], e)

    return None


def parse_geometry_series(series: pd.Series) -> pd.Series:
    non_null = series.dropna()
    if non_null.empty:
        return pd.Series([None] * len(series), index=series.index, dtype="object")

    sample = non_null.iloc[0]

    if hasattr(sample, "geom_type"):
        return series

    if isinstance(sample, (bytes, memoryview)):
        try:
            values = series.to_numpy(copy=False)
            if any(isinstance(v, memoryview) for v in values):
                values = series.map(
                    lambda v: bytes(v) if isinstance(v, memoryview) else v
                ).to_numpy()
            parsed = shapely.from_wkb(values, on_invalid="ignore")
            return pd.Series(parsed, index=series.index, dtype="object")
        except Exception as e:
            logger.debug("parse_geometry_series: WKB bytes fast path fallito: %s", e)
            return series.apply(parse_geometry)

    sample_str = str(sample).strip()
    sample_upper = sample_str.upper()

    if any(sample_upper.startswith(prefix) for prefix in WKT_PREFIXES):
        try:
            values = series.map(
                lambda v: None if pd.isna(v) or v == "" or v == "None" else str(v).strip()
            ).to_numpy()
            parsed = shapely.from_wkt(values, on_invalid="ignore")
            return pd.Series(parsed, index=series.index, dtype="object")
        except Exception as e:
            logger.debug("parse_geometry_series: WKT fast path fallito: %s", e)
            return series.apply(parse_geometry)

    if looks_like_wkb_hex(sample_str):
        try:
            values = series.map(
                lambda v: None if pd.isna(v) or v == "" or v == "None" else str(v).strip()
            ).to_numpy()
            parsed = shapely.from_wkb(values, hex=True, on_invalid="raise")
            return pd.Series(parsed, index=series.index, dtype="object")
        except Exception as e:
            logger.debug("parse_geometry_series: WKB hex fast path fallito: %s", e)
            return series.apply(parse_geometry)

    return series.apply(parse_geometry)


def parse_wkb_hex(value_str: str) -> Optional[Any]:
    try:
        return wkb.loads(value_str, hex=True)
    except Exception as e:
        logger.debug("parse_wkb_hex: WKB standard fallito: %s", e)

    try:
        if len(value_str) > 18 and value_str[8:10] == "20":
            wkb_pulito = (
                value_str[:2]
                + value_str[2:10].replace("20", "00")
                + value_str[18:]
            )
            return wkb.loads(wkb_pulito, hex=True)
    except Exception as e:
        logger.debug("parse_wkb_hex: EWKB con rimozione SRID fallito: %s", e)

    return None


def extract_srid_from_ewkb(value_str: str) -> Optional[int]:
    if not looks_like_wkb_hex(value_str) or len(value_str) < 18:
        return None

    try:
        if value_str[8:10] == "20":
            srid_hex = value_str[10:18]
            srid_bytes = bytes.fromhex(srid_hex)
            srid = int.from_bytes(srid_bytes, byteorder="little")
            if srid > 0:
                return srid
    except (ValueError, IndexError) as e:
        logger.debug("extract_srid_from_ewkb: estrazione SRID fallita: %s", e)

    return None
