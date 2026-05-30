"""
persist.py — Persistenza/lettura di un bundle ``plenora.indoor.bundle``.

``persist_indoor_bundle`` scrive le sezioni del bundle (manifest con
``schema_descriptor``, validation, renderers, ``{site}/{layer}.geojson``) su
object storage riusando il layout per-sezione (``storage.save_bundle_files``),
con meta ``has_gdb=False`` + discriminatore ``format``.
``load_indoor_bundle_payload`` e' l'inverso: ricompone il payload dalle sezioni
per il publish/validate. Nessun ``.gdb`` / GDAL coinvolto.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.imdf import storage as _imdf_storage
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger

logger = get_logger(__name__)


def persist_indoor_bundle(
    payload: JsonObject,
    task_id: str,
    project_id: Optional[str],
) -> JsonObject:
    """Persiste un bundle ``plenora.indoor.bundle`` (v1.0) su storage canonico.

    Path NUOVO, parallelo a ``persist_imdf_bundle`` (legacy ZIP/GDB). Riusa il
    **layout storage per-sezione** (``save_bundle_files``) cosi' i reader
    esistenti (router GET/list) e il publish lo consumano **senza ``.gdb``**:
      - ``manifest.json``  <- ``payload['manifest']`` (include ``schema_descriptor``)
      - ``validation.json`` / ``renderers.json`` se presenti
      - ``{site}/{layer}.geojson`` per ogni layer (FeatureCollection)
      - meta con ``has_gdb=False`` + discriminatore ``format`` per distinguere
        i bundle nuovi da quelli legacy.

    Nessun tmp dir / cleanup: il payload e' gia' in RAM come JSON.
    """
    manifest: Dict[str, Any] = payload.get("manifest") or {}
    metadata: Dict[str, Any] = payload.get("metadata") or {}
    site_ids = metadata.get("site_ids") or manifest.get("site_ids") or []
    site_id = site_ids[0] if site_ids else ""
    layers: Dict[str, Any] = payload.get("layers") or {}
    validation = payload.get("validation")
    renderers = payload.get("renderers")

    files: Dict[str, bytes] = {
        "manifest.json": json.dumps(manifest).encode("utf-8"),
    }
    if validation is not None:
        files["validation.json"] = json.dumps(validation).encode("utf-8")
    if renderers is not None:
        files["renderers.json"] = json.dumps(renderers).encode("utf-8")
    if site_id:
        for layer_name, feature_collection in layers.items():
            files[f"{site_id}/{layer_name}.geojson"] = json.dumps(
                feature_collection
            ).encode("utf-8")

    meta: Dict[str, Any] = {
        "project_id":     project_id,
        "task_id":        task_id,
        "site_id":        site_id,
        "site_ids":       site_ids,
        "saved_at":       datetime.now(timezone.utc).isoformat(),
        "has_gdb":        False,
        "has_ifc":        False,
        "has_renderers":  renderers is not None,
        # Discriminatore: i reader/publish distinguono il formato nuovo dal
        # legacy via questo campo (has_gdb=False non basta come segnale).
        "format":         payload.get("schema"),
        "bundle_version": str(payload.get("version") or ""),
        "layer_counts":   manifest.get("layer_counts"),
    }
    if isinstance(validation, dict):
        meta["validation_summary"] = {
            "passed":         validation.get("passed"),
            "total_errors":   validation.get("total_errors"),
            "total_warnings": validation.get("total_warnings"),
        }

    storage_keys = _imdf_storage.save_bundle_files(task_id, files, meta)
    logger.info(
        f"[{task_id}] indoor bundle persistito (sezioni, no GDB; "
        f"site={site_id}, layers={len(layers)})"
    )
    return {"storage_keys": storage_keys, "meta": meta}


def load_indoor_bundle_payload(task_id: str) -> Optional[JsonObject]:
    """Ricompone il payload ``plenora.indoor.bundle`` dalle sezioni su storage.

    Inverso di ``persist_indoor_bundle``: legge ``manifest.json`` (con
    ``schema_descriptor``) + ``{site}/{layer}.geojson`` per ogni layer
    dichiarato + validation/renderers, e ricostruisce il payload consumabile da
    ``bundle_loader.parse_indoor_bundle`` / ``bundle_geo``. Usato dal publish:
    niente ``.gdb``, niente vsimem.

    Returns:
        Il payload, o ``None`` se manca il manifest (bundle assente/legacy).
    """
    manifest = _imdf_storage.read_manifest(task_id)
    if not isinstance(manifest, dict):
        return None
    descriptor = manifest.get("schema_descriptor") or {}
    meta = _imdf_storage.get_meta_only(task_id) or {}

    site_ids = (
        manifest.get("site_ids")
        or meta.get("site_ids")
        or ([meta["site_id"]] if meta.get("site_id") else [])
    )
    site_id = site_ids[0] if site_ids else ""

    layers: Dict[str, Any] = {}
    if site_id:
        for layer_name in (descriptor.get("layers") or {}):
            fc = _imdf_storage.read_geojson(task_id, f"{site_id}/{layer_name}.geojson")
            if isinstance(fc, dict):
                layers[layer_name] = fc

    return {
        "schema":     meta.get("format") or "plenora.indoor.bundle",
        "version":    str(meta.get("bundle_version") or "1.0"),
        "metadata":   {"site_ids": site_ids},
        "manifest":   manifest,
        "validation": _imdf_storage.read_validation(task_id),
        "renderers":  _imdf_storage.read_renderers(task_id),
        "layers":     layers,
    }


# Tombstone (QUERY-1 step 3): `persist_imdf_bundle_to_disk` rimosso.
# Era il materializer locale `IMDF_BUNDLES_DIR/{task_id}/`. Tutti i
# caller di produzione + script `imdf_real_e2e_check.py` usano
# `persist_imdf_bundle` (storage-direct).
