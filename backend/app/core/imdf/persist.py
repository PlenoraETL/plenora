"""
persist.py — Persistenza di un ImdfBundle (IMDF-3 RAM-first).

IMDF-3 ha invertito il contratto:
  - prima: persist = local materializer puro (writes to disk),
    storage.save_bundle separato (worker orchestra entrambi)
  - ora:   persist = orchestratore RAM-first verso storage canonico.
    `bundle.files` -> object storage come oggetti individuali;
    `.gdb` gia' ricomposto dal loader come zip RAM -> upload come
    `_gdb.zip`; meta.json computato in RAM e uploadato.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.imdf import storage as _imdf_storage
from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _count_features_from_bytes(data: Optional[bytes]) -> Optional[int]:
    """Conta `features` da un blob GeoJSON in memoria.
    Ritorna None su parse-error / formato non-FeatureCollection."""
    if not data:
        return None
    try:
        obj = json.loads(data)
        feats = obj.get("features") if isinstance(obj, dict) else None
        return len(feats) if isinstance(feats, list) else None
    except Exception as e:
        logger.warning(f"_count_features_from_bytes: {e}")
        return None


def persist_imdf_bundle(
    bundle,
    task_id: str,
    project_id: Optional[str],
) -> JsonObject:
    """Persiste un ImdfBundle verso storage canonico (IMDF-3 RAM-first).

    Behavior:
      - Per ogni componente in ``bundle.files`` (manifest, validation,
        renderers, geojson sotto site_id) -> upload come oggetto
        individuale su storage sotto ``{PREFIX}/{task_id}/files/{name}``.
        NO shutil.move, NO local dir.
      - Se ``bundle.has_gdb`` -> upload di ``bundle.gdb_zip_bytes`` come
        ``{PREFIX}/{task_id}/_gdb.zip``.
      - Se ``bundle.has_ifc`` -> upload dei bytes IFC
        come ``{PREFIX}/{task_id}/files/{ifc_name}``.
      - Computa ``plenora_metadata.json`` dai counts geojson + flags
        + validation_summary -> upload come ``{task_id}.meta.json``.
      - Cleanup ``bundle.tmp_dir`` SEMPRE garantito dal finally
        (anti-regression orfani `/tmp/imdf_XXXX/`).

    Args:
        bundle: ImdfBundle da ``extract_imdf_zip_bytes``.
        task_id: id univoco del bundle.
        project_id: project_id del canvas (per filtering lato list).

    Returns:
        dict con chiavi:
          - storage_keys: dict ritornato da ``save_bundle_files``
            (``file_keys``, ``meta_key``, ``gdb_zip_key``)
          - meta: dict del plenora_metadata uploaded

    Raises:
        Propaga OSError/RuntimeError da storage. ``bundle.tmp_dir`` e'
        comunque cancellato dal finally.
    """
    try:
        ifc_bytes: Optional[bytes] = bundle.ifc_bytes
        ifc_filename: Optional[str] = None
        if ifc_bytes is not None:
            ifc_filename = f"{bundle.site_id or 'bundle'}.ifc"

        gdb_zip_bytes: Optional[bytes] = bundle.gdb_zip_bytes

        # Meta dict computato in RAM dai counts in bundle.files (geojson).
        meta: Dict[str, Any] = {
            "project_id":    project_id,
            "task_id":       task_id,
            "site_id":       bundle.site_id,
            "saved_at":      datetime.now(timezone.utc).isoformat(),
            "has_gdb":       bundle.has_gdb,
            "has_ifc":       bundle.has_ifc,
            "has_renderers": bundle.has_renderers,
        }

        # Validation summary dai bytes RAM-first (no filesystem read).
        try:
            val_data = bundle.files.get("validation.json")
            if val_data:
                val = json.loads(val_data) or {}
                meta["validation_summary"] = {
                    "passed":         val.get("passed"),
                    "total_errors":   val.get("total_errors"),
                    "total_warnings": val.get("total_warnings"),
                }
        except Exception as ve:
            logger.warning(f"[{task_id}] validation_summary read: {ve}")

        # Counts layer dai geojson in memoria (no disk).
        if bundle.site_id:
            for layer_attr, fname in (
                ("building_count", "building.geojson"),
                ("level_count",    "level.geojson"),
                ("unit_count",     "unit.geojson"),
            ):
                key = f"{bundle.site_id}/{fname}"
                cnt = _count_features_from_bytes(bundle.files.get(key))
                if cnt is not None:
                    meta[layer_attr] = cnt

        # Upload tutto su storage canonico in un colpo solo.
        try:
            storage_keys = _imdf_storage.save_bundle_files(
                task_id,
                bundle.files,
                meta,
                gdb_zip_bytes=gdb_zip_bytes,
                ifc_bytes=ifc_bytes,
                ifc_filename=ifc_filename,
            )
        except Exception as up_err:
            # Wording preservato dall'ops-grep contract (alert/dashboard
            # storiche): la stringa "imdf storage upload fallito (non-fatal"
            # e' un contract pubblico verso ops.
            logger.warning(
                f"[{task_id}] imdf storage upload fallito (non-fatal, "
                f"bundle non disponibile per altri nodi): {up_err}"
            )
            raise

        logger.info(
            f"[{task_id}] IMDF bundle persistito su storage "
            f"(site={bundle.site_id}, files={len(bundle.files)})"
        )

        return {"storage_keys": storage_keys, "meta": meta}

    finally:
        # Cleanup SEMPRE garantito (storico anti-orfani `/tmp/imdf_XXXX/`).
        try:
            bundle.cleanup()
        except Exception as ce:
            logger.warning(f"[{task_id}] bundle.cleanup in finally: {ce}")


# Tombstone (QUERY-1 step 3): `persist_imdf_bundle_to_disk` rimosso.
# Era il legacy local materializer (IMDF_BUNDLES_DIR/{task_id}/) lasciato
# per i test/script pre-IMDF-3. Tutti i caller di produzione + script
# `imdf_real_e2e_check.py` migrati a `persist_imdf_bundle` (storage-direct).
