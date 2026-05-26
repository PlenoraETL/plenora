"""
persist.py — Persistenza di un ImdfBundle (IMDF-3 RAM-first).

IMDF-3 ha invertito il contratto:
  - prima: persist = local materializer puro (writes to disk),
    storage.save_bundle separato (worker orchestra entrambi)
  - ora:   persist = orchestratore RAM-first verso storage canonico.
    `bundle.files` -> object storage come oggetti individuali;
    `.gdb` (path-only by design pyogrio) -> zip in memoria + upload
    come `_gdb.zip`; meta.json computato in RAM e uploadato.

Niente piu' `IMDF_BUNDLES_DIR` come canonical state — la cache
locale e' solo idratata on-demand dai reader (vedi
`storage.ensure_local_bundle`, IMDF-4 endpoint serve).
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
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


def _zip_gdb_dir_to_bytes(gdb_dir: Path) -> bytes:
    """Crea uno zip in memoria del File Geodatabase directory.

    Adapter legacy: il .gdb e' un formato directory letto da pyogrio
    via filesystem; lo persistiamo come singolo oggetto zip su storage
    per upload/download atomico. IMDF-4 puo' valutare alternative
    (e.g. fdb cloud-native) ma per ora resta zip.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in gdb_dir.rglob("*"):
            if p.is_file():
                arcname = gdb_dir.name + "/" + str(p.relative_to(gdb_dir))
                zf.write(p, arcname=arcname)
    return buf.getvalue()


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
      - Se ``bundle.has_gdb`` -> zip in memoria del .gdb dir (legacy
        adapter pyogrio) + upload come ``{PREFIX}/{task_id}/_gdb.zip``.
      - Se ``bundle.has_ifc`` -> read bytes dal file .ifc + upload
        come ``{PREFIX}/{task_id}/files/{ifc_name}``.
      - Computa ``plenora_metadata.json`` dai counts geojson + flags
        + validation_summary -> upload come ``{task_id}.meta.json``.
      - Cleanup ``bundle.tmp_dir`` SEMPRE garantito dal finally
        (anti-regression orfani `/tmp/imdf_XXXX/`).

    Args:
        bundle: ImdfBundle (da ``extract_imdf_zip_bytes`` o
            ``extract_imdf_zip``).
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
        # IMDF-6: bytes pre-collezionati dall'extract bytes-first.
        # Fallback al path-mode legacy (`extract_imdf_zip(path)`) che
        # popola `*_path` su tmp_dir e richiede zip/read on-demand.
        ifc_bytes: Optional[bytes] = bundle.ifc_bytes
        ifc_filename: Optional[str] = None
        if ifc_bytes is not None:
            # Filename: best-effort dalla site_id (`{site}.ifc`).
            ifc_filename = (
                bundle.ifc_path.name if bundle.ifc_path is not None
                else f"{bundle.site_id or 'bundle'}.ifc"
            )
        elif bundle.ifc_path is not None and bundle.ifc_path.exists():
            ifc_bytes = bundle.ifc_path.read_bytes()
            ifc_filename = bundle.ifc_path.name

        gdb_zip_bytes: Optional[bytes] = bundle.gdb_zip_bytes
        if gdb_zip_bytes is None and bundle.gdb_path is not None and bundle.gdb_path.exists():
            gdb_zip_bytes = _zip_gdb_dir_to_bytes(bundle.gdb_path)

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
