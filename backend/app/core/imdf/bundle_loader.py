"""
bundle_loader.py — consumer del contratto ``plenora.indoor.bundle``.

Sostituisce, lato lettura, il round-trip GDAL del path legacy
(``extract_imdf_zip_bytes`` -> ``_gdb.zip`` -> ``open_gdb_in_vsimem`` ->
``load_imdf_layers``). Il convertitore (controllato) emette un bundle
**self-describing**: ``manifest.schema_descriptor`` dichiara per ogni layer la
geometria, le chiavi ``identity`` (match_columns per l'upsert) e i ``fields``,
dove **ogni campo porta un ``source``** = JSON-path dentro ``feature.properties``
(es. ``"id"``, ``"name.it"``, ``"extension.area"``).

Il loader e' quindi **100% generico**: nessun layer/campo/chiave/CRS hardcoded
(addio ``EXPECTED_LAYERS`` + ``DEFAULT_MATCH_COLUMNS``). Per ogni campo:
``feature.properties[source]`` -> colonna del tipo dichiarato.

Questo modulo e' **puro** (nessun import pandas/geopandas, rispetta il
dataframe import boundary): produce record piatti + geometria GeoJSON + identity
per layer. L'assemblaggio in GeoDataFrame avviene al boundary ``core/dataframes``
(step successivo del refactor, vedi design note 13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Tuple

from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger

logger = get_logger(__name__)

BUNDLE_SCHEMA = "plenora.indoor.bundle"
DESCRIPTOR_SCHEMA = "plenora.indoor.schema"
SUPPORTED_BUNDLE_VERSIONS = frozenset({"1.0"})
SUPPORTED_DESCRIPTOR_VERSIONS = frozenset({"1.0"})


class IndoorBundleError(ValueError):
    """Payload non conforme al contratto ``plenora.indoor.bundle``."""


@dataclass(frozen=True)
class LayerField:
    """Un campo dichiarato dal descrittore: ``source`` -> ``name`` tipizzato."""

    name: str
    source: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class LayerRecords:
    """Layer appiattito, pronto per l'assemblaggio in GeoDataFrame.

    ``records`` e ``geometries`` sono allineati per indice. Tutto puro-python:
    nessuna dipendenza geospaziale qui.
    """

    name: str
    geometry_type: str
    has_z: bool
    identity: Tuple[str, ...]
    fields: Tuple[LayerField, ...]
    records: List[JsonObject]
    geometries: List[Optional[JsonObject]]
    renderer_ref: Optional[str]


def parse_indoor_bundle(payload: Any) -> JsonObject:
    """Valida l'involucro del bundle e ritorna il payload.

    Controlla ``schema``/``version`` del bundle e del descrittore embedded.
    Non valida i dati delle feature (responsabilita' del validator/QA).

    Raises:
        IndoorBundleError: se schema/version sono assenti o non supportati, o se
        manca ``manifest.schema_descriptor``.
    """
    if not isinstance(payload, dict):
        raise IndoorBundleError("bundle non e' un oggetto JSON")

    schema = payload.get("schema")
    if schema != BUNDLE_SCHEMA:
        raise IndoorBundleError(
            f"schema bundle inatteso: {schema!r} (atteso {BUNDLE_SCHEMA!r})"
        )
    version = str(payload.get("version", ""))
    if version not in SUPPORTED_BUNDLE_VERSIONS:
        raise IndoorBundleError(
            f"versione bundle non supportata: {version!r} "
            f"(supportate: {sorted(SUPPORTED_BUNDLE_VERSIONS)})"
        )

    descriptor = (payload.get("manifest") or {}).get("schema_descriptor")
    if not isinstance(descriptor, dict):
        raise IndoorBundleError("manifest.schema_descriptor mancante o non valido")
    d_schema = descriptor.get("schema")
    if d_schema != DESCRIPTOR_SCHEMA:
        raise IndoorBundleError(
            f"schema descrittore inatteso: {d_schema!r} (atteso {DESCRIPTOR_SCHEMA!r})"
        )
    d_version = str(descriptor.get("version", ""))
    if d_version not in SUPPORTED_DESCRIPTOR_VERSIONS:
        raise IndoorBundleError(
            f"versione descrittore non supportata: {d_version!r} "
            f"(supportate: {sorted(SUPPORTED_DESCRIPTOR_VERSIONS)})"
        )
    return payload


def _get_by_path(properties: Any, source: str) -> Any:
    """Risolve un ``source`` dotted (es. ``extension.area``) in
    ``feature.properties``. Ritorna None se un segmento manca."""
    cur: Any = properties
    for segment in source.split("."):
        if not isinstance(cur, dict) or segment not in cur:
            return None
        cur = cur[segment]
    return cur


def _coerce(value: Any, declared_type: str) -> Any:
    """Coerce best-effort verso il tipo dichiarato. None resta None.

    La data-quality (null su campo non-nullable, ecc.) NON e' compito del
    loader: e' del validator. Qui solo conversione di tipo; su fallimento si
    tiene il valore grezzo con un debug log (fail-soft)."""
    if value is None:
        return None
    try:
        if declared_type == "string":
            return value if isinstance(value, str) else str(value)
        if declared_type == "int":
            return int(value)
        if declared_type == "float":
            return float(value)
        if declared_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            return str(value).strip().lower() in {"1", "true", "si", "si'", "yes"}
        if declared_type == "date":
            # Tenuto come stringa ISO: l'assemblaggio dataframe/ArcGIS la parsa
            # al boundary. Evitiamo dipendenze datetime/format qui.
            return value if isinstance(value, str) else str(value)
    except (TypeError, ValueError) as e:
        logger.debug(f"coerce {declared_type} fallita per {value!r}: {e}")
        return value
    return value


def _parse_fields(raw_fields: Any) -> Tuple[LayerField, ...]:
    fields: List[LayerField] = []
    for f in raw_fields or ():
        if not isinstance(f, dict) or "name" not in f or "source" not in f:
            continue
        fields.append(
            LayerField(
                name=f["name"],
                source=f["source"],
                type=f.get("type", "string"),
                nullable=bool(f.get("nullable", True)),
            )
        )
    return tuple(fields)


def iter_layer_records(payload: JsonObject) -> Iterator[LayerRecords]:
    """Per ogni layer dichiarato nel descrittore, appiattisce le feature in
    record + geometrie usando i ``source`` path. Generico: itera cio' che il
    descrittore dichiara, niente lista di layer hardcoded.

    Un layer dichiarato ma assente da ``payload['layers']`` viene saltato con
    un warning (il delta lo cattura il validator).
    """
    descriptor = payload["manifest"]["schema_descriptor"]
    layers_data = payload.get("layers") or {}

    for name, layer_desc in (descriptor.get("layers") or {}).items():
        fc = layers_data.get(name)
        if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
            logger.warning(f"layer '{name}' dichiarato nel descrittore ma assente/non-FC")
            continue

        fields = _parse_fields(layer_desc.get("fields"))
        geometry = layer_desc.get("geometry") or {}
        identity = tuple(layer_desc.get("identity") or ())

        records: List[JsonObject] = []
        geometries: List[Optional[JsonObject]] = []
        for feature in fc.get("features") or ():
            props = feature.get("properties") or {}
            row: JsonObject = {
                fld.name: _coerce(_get_by_path(props, fld.source), fld.type)
                for fld in fields
            }
            records.append(row)
            geometries.append(feature.get("geometry"))

        yield LayerRecords(
            name=name,
            geometry_type=str(geometry.get("type") or ""),
            has_z=bool(geometry.get("has_z")),
            identity=identity,
            fields=fields,
            records=records,
            geometries=geometries,
            renderer_ref=layer_desc.get("renderer_ref"),
        )


__all__ = [
    "BUNDLE_SCHEMA",
    "DESCRIPTOR_SCHEMA",
    "SUPPORTED_BUNDLE_VERSIONS",
    "SUPPORTED_DESCRIPTOR_VERSIONS",
    "IndoorBundleError",
    "LayerField",
    "LayerRecords",
    "parse_indoor_bundle",
    "iter_layer_records",
]
