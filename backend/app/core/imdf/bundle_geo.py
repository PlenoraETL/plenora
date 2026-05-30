"""
bundle_geo.py — assembla i layer di un ``plenora.indoor.bundle`` in
GeoDataFrame, pronti per ``arcgis_uploader.upload_imdf_layers``.

Ponte tra il core puro (``bundle_loader.iter_layer_records``, nessuna
dipendenza geospaziale) e il boundary ``core/dataframes`` (dove geopandas e'
allowlistato). Questo modulo NON importa geopandas direttamente: delega a
``geodataframe_from_geojson_records``.

Sostituisce ``loader.load_imdf_layers`` nel nuovo path: niente GDB, niente
pyogrio/vsimem. CRS e tipi vengono dal descrittore; ``identity`` diventa le
match_columns per l'upsert.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TypedDict

from app.core.dataframes.geodata import geodataframe_from_geojson_records
from app.core.imdf.bundle_loader import iter_layer_records
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LayerGeoEntry(TypedDict):
    """Layer pronto per il publish: GeoDataFrame + identity (match_columns) +
    renderer_ref."""

    gdf: Any  # GeoDataFrame (geopandas non importato qui per il boundary)
    identity: Tuple[str, ...]
    renderer_ref: Optional[str]


# Alias semantici (evitano `Dict[...]` nudo nelle return annotation, come
# JsonObject/StringMap altrove). name -> entry / name -> GeoDataFrame.
LayerGeoEntries = Dict[str, LayerGeoEntry]
LayerFrames = Dict[str, Any]


def _descriptor_crs(payload: JsonObject) -> str:
    descriptor = (payload.get("manifest") or {}).get("schema_descriptor") or {}
    return descriptor.get("crs") or "EPSG:4326"


def build_layer_geodataframes(
    payload: JsonObject,
    *,
    drop_z: bool = True,
) -> LayerGeoEntries:
    """Trasforma ogni layer dichiarato dal descrittore in un GeoDataFrame.

    Returns:
        ``{layer_name: {"gdf": GeoDataFrame, "identity": tuple[str, ...],
        "renderer_ref": str | None}}``. I layer dichiarati ma assenti dai dati
        sono saltati a monte da ``iter_layer_records``.
    """
    crs = _descriptor_crs(payload)
    out: LayerGeoEntries = {}
    for lr in iter_layer_records(payload):
        gdf = geodataframe_from_geojson_records(
            lr.records, lr.geometries, crs=crs, drop_z=drop_z,
        )
        out[lr.name] = {
            "gdf": gdf,
            "identity": lr.identity,
            "renderer_ref": lr.renderer_ref,
        }
        logger.debug(
            f"bundle_geo: layer '{lr.name}' -> GeoDataFrame "
            f"({len(lr.records)} feat, {len(lr.fields)} col, identity={lr.identity})"
        )
    return out


def layer_geodataframes_only(payload: JsonObject, *, drop_z: bool = True) -> LayerFrames:
    """Variante che ritorna solo ``{layer_name: GeoDataFrame}`` — shape compat
    con ``loader.load_imdf_layers`` per il consumo da ``upload_imdf_layers``."""
    return {
        name: bundle["gdf"]
        for name, bundle in build_layer_geodataframes(payload, drop_z=drop_z).items()
    }


__all__ = ["build_layer_geodataframes", "layer_geodataframes_only"]
