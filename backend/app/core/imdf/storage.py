"""Storage IMDF bundle su object storage cloud-agnostic.

Chiude R2 del multi-host audit: i bundle IMDF non vivono piu' solo su
local FS. Pattern "lazy local cache":

  - S3 = fonte di verita' persistente (post-task)
  - Local FS (IMDF_BUNDLES_DIR/{task_id}/) = cache performance
  - Cache miss → download ZIP da S3 + extract local → cache warm

Vantaggi rispetto a "full S3 refactor":
  - Gli endpoint reader esistenti funzionano invariati (leggono local FS)
  - Al primo accesso su un nodo nuovo, paga ~1-5s download + extract
  - Subsequent reads: cache hit, zero latency extra
  - Lista bundle: legge solo .meta.json piccoli da S3 (no download full zip)

Convenzione S3 keys:
  - `imdf-bundles/{task_id}.zip`      → bundle completo (GDB/GeoJSON/IFC/...)
  - `imdf-bundles/{task_id}.meta.json` → solo plenora_metadata.json
    (separato per list/summary endpoint senza download del zip)

Cleanup cache locale: beat_scheduler._cleanup_imdf_bundles rotea
IMDF_BUNDLES_DIR ogni 30min (TTL = IMDF_BUNDLE_TTL_HOURS, default 48h).
Cancellazione S3: eager al delete endpoint + lifecycle rule safety net
(TODO AWS config).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger


logger = get_logger(__name__)


# Prefix S3 condiviso (distinto da urby_*.dump dei backup + eseguibili-outputs/).
PREFIX = "imdf-bundles"
META_SUFFIX = ".meta.json"
ZIP_SUFFIX = ".zip"


def _storage():
    """Lazy load della factory cloud."""
    from app.shared.cloud_backends import get_object_storage
    return get_object_storage()


def _zip_key(task_id: str) -> str:
    return f"{PREFIX}/{task_id}{ZIP_SUFFIX}"


def _meta_key(task_id: str) -> str:
    return f"{PREFIX}/{task_id}{META_SUFFIX}"


def _local_dir(task_id: str) -> Path:
    from app.config import settings as _settings
    return _settings.IMDF_BUNDLES_DIR / task_id


# =============================================================================
# Upload (worker side, post-extract)
# =============================================================================


def save_bundle(task_id: str, local_dir: Path, meta: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Zippa `local_dir` (bundle estratto dal worker) e carica su S3.

    Upload anche il `plenora_metadata.json` separatamente come
    `{task_id}.meta.json` per list endpoint veloci (no download zip).

    Args:
        task_id: id univoco per l'oggetto S3
        local_dir: cartella gia' popolata dal persist locale
        meta: dict di metadata (se None, lo legge da `plenora_metadata.json`
            nella dir)

    Returns:
        dict con chiavi:
          - zip_key: key S3 dello zip
          - meta_key: key S3 del meta

    Raises:
        FileNotFoundError: local_dir non esiste
        OSError: zip/upload fallito (propagato)
    """
    if not local_dir.exists() or not local_dir.is_dir():
        raise FileNotFoundError(f"Bundle dir non trovata: {local_dir}")

    # Leggi meta se non fornito (usa file scritto dal persist step precedente).
    if meta is None:
        meta_path = local_dir / "plenora_metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[{task_id}] meta parse fallito: {e}; upload senza meta.json")
                meta = None

    storage = _storage()

    # Zip su tempfile sul worker (safe: worker ha il suo filesystem).
    with tempfile.NamedTemporaryFile(
        prefix=f"imdf_{task_id}_", suffix=".zip", delete=False
    ) as tmp:
        tmp_zip = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for p in local_dir.rglob("*"):
                if p.is_file():
                    arcname = p.relative_to(local_dir)
                    zf.write(p, arcname=str(arcname))

        zk = _zip_key(task_id)
        storage.upload(tmp_zip, zk)
        logger.info(f"[{task_id}] imdf bundle zip uploaded: {zk} ({tmp_zip.stat().st_size} bytes)")
    finally:
        try:
            tmp_zip.unlink()
        except OSError:
            pass

    # Meta JSON separato (se disponibile) — utile per list veloce
    mk = _meta_key(task_id)
    if meta is not None:
        with tempfile.NamedTemporaryFile(
            prefix=f"imdf_{task_id}_", suffix=".meta.json", delete=False, mode="w", encoding="utf-8"
        ) as tmp_meta:
            json.dump(meta, tmp_meta, indent=2)
            tmp_meta_path = Path(tmp_meta.name)
        try:
            storage.upload(tmp_meta_path, mk)
            logger.info(f"[{task_id}] imdf meta uploaded: {mk}")
        finally:
            try:
                tmp_meta_path.unlink()
            except OSError:
                pass

    return {"zip_key": _zip_key(task_id), "meta_key": mk}


# =============================================================================
# Read (endpoint side)
# =============================================================================


def ensure_local_bundle(task_id: str) -> Path:
    """Garantisce che il bundle sia estratto localmente. Cache miss → pull da S3.

    Policy:
      - Se `IMDF_BUNDLES_DIR/{task_id}/` esiste e non e' vuota → cache hit.
      - Altrimenti → download zip da S3, extract in-place → cache warm.
      - Se neanche S3 ha lo zip → FileNotFoundError (bundle veramente assente).

    Side effect: scrive su IMDF_BUNDLES_DIR locale. Concorrenza multi-process
    sullo stesso task_id sul medesimo nodo e' rara (ogni task_id e' univoco e
    il primo accessor vince); in caso di race, `shutil.move` resta idempotente
    per la shape "target esiste gia'".

    Returns:
        Path della directory locale con il bundle estratto.

    Raises:
        FileNotFoundError: bundle non presente ne' in local ne' S3.
    """
    local = _local_dir(task_id)
    if local.exists() and any(local.iterdir()):
        return local

    # Cache miss: tentativo di hydration da S3.
    storage = _storage()
    zk = _zip_key(task_id)

    # Download zip su tempfile locale, poi extract.
    with tempfile.NamedTemporaryFile(
        prefix=f"imdf_hydrate_{task_id}_", suffix=".zip", delete=False
    ) as tmp:
        tmp_zip = Path(tmp.name)

    try:
        try:
            storage.download(zk, tmp_zip)
        except Exception as e:
            # Tipicamente NoSuchKey dal client S3 → bundle inesistente.
            logger.warning(f"[{task_id}] imdf storage hydration failed: {e}")
            raise FileNotFoundError(f"Bundle {task_id} non presente su storage: {e}")

        local.mkdir(parents=True, exist_ok=True)
        from app.utils.zip_safe import safe_extractall
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            # safe_extractall: zip slip + zip bomb pre-extract.
            # I bundle IMDF tipici sono ~20-100MB, ben sotto i 500MB
            # default. Override possibile via ZIP_MAX_UNCOMPRESSED_BYTES
            # se in futuro emergono bundle "grandi venue" (aeroporti).
            safe_extractall(zf, local)

        logger.info(f"[{task_id}] imdf bundle hydrated from storage -> {local}")
        return local
    finally:
        try:
            tmp_zip.unlink()
        except OSError:
            pass


def get_meta_only(task_id: str) -> Optional[Dict[str, Any]]:
    """Legge SOLO `plenora_metadata.json` da S3 (senza download zip).

    Usato dal list endpoint per enumerare bundle senza costi di estrazione.
    Ritorna None se il meta non esiste su S3 (bundle non ancora pubblicato
    o solo local-cached).
    """
    storage = _storage()
    mk = _meta_key(task_id)
    try:
        stream = storage.get_object_stream(mk)
        body_bytes = b"".join(stream["body"])
        return json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        logger.debug(f"[{task_id}] meta only read skipped: {e}")
        return None


def list_task_ids() -> List[str]:
    """Elenca task_id dei bundle presenti su S3 (via meta.json).

    Usato dal list endpoint per vedere bundle da tutti i nodi (non solo
    quelli cached sul nodo corrente).
    """
    storage = _storage()
    objs = storage.list_objects(f"{PREFIX}/")
    task_ids = []
    for o in objs:
        key = o["key"]
        # Filtra solo i meta.json per dedup (evita doppio-conteggio zip+meta)
        if key.endswith(META_SUFFIX):
            # Estrai task_id: "imdf-bundles/task-abc.meta.json" -> "task-abc"
            basename = key[len(PREFIX) + 1:]  # strip "imdf-bundles/"
            task_id = basename[:-len(META_SUFFIX)]
            task_ids.append(task_id)
    return task_ids


def delete_bundle(task_id: str) -> int:
    """Cancella zip + meta da S3. Ritorna count oggetti eliminati."""
    storage = _storage()
    deleted = 0
    for key in (_zip_key(task_id), _meta_key(task_id)):
        try:
            storage.delete(key)
            deleted += 1
        except Exception as e:
            logger.warning(f"delete {key} fallito (non-fatal): {e}")

    # Cleanup cache locale sul nodo corrente (se presente).
    local = _local_dir(task_id)
    if local.exists():
        try:
            shutil.rmtree(local, ignore_errors=True)
        except OSError as e:
            logger.warning(f"[{task_id}] cleanup local cache fallito: {e}")

    return deleted
