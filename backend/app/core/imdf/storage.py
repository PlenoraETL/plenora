"""
Storage IMDF bundle su object storage cloud-agnostic.

Chiude R2 del multi-host audit: i bundle IMDF non vivono piu' solo su
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import List, Optional

from app.infrastructure.storage.cloud_backends import object_storage_enabled
from app.shared.contracts.payloads import JsonObject, StringMap
from app.shared.resources.file_cleanup import rmtree_safe
from app.shared.resources.storage_layout import IMDF_BUNDLES_PREFIX
from app.utils.zip_safe import safe_extractall
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Module-level alias kept for internal call sites and tests.
PREFIX = IMDF_BUNDLES_PREFIX
META_SUFFIX = ".meta.json"
ZIP_SUFFIX = ".zip"


def _storage():
    """Lazy load della factory cloud runtime.

    Solleva `ObjectStorageDisabledError` se la capability globale e' off:
    i caller list/get_meta_only/list_task_ids degradano graceful
    (cache locale only) catturando l'eccezione; save_bundle invece
    deve fallire perche' il pubblico richiede deve completare l'upload."""
    from app.infrastructure.storage.cloud_backends import get_runtime_object_storage
    return get_runtime_object_storage()


def _zip_key(task_id: str) -> str:
    return f"{PREFIX}/{task_id}{ZIP_SUFFIX}"


def _meta_key(task_id: str) -> str:
    return f"{PREFIX}/{task_id}{META_SUFFIX}"


def _local_dir(task_id: str) -> Path:
    from app.config import settings as _settings
    return _settings.IMDF_BUNDLES_DIR / task_id




def save_bundle(task_id: str, local_dir: Path, meta: Optional[JsonObject] = None) -> StringMap:
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

    # Leggi meta se non fornito (usa file scritto dal persist step precedente
    if meta is None:
        meta_path = local_dir / "plenora_metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[{task_id}] meta parse fallito: {e}; upload senza meta.json")
                meta = None

    storage = _storage()

    # Build ZIP in-memory via BytesIO, upload diretto via upload_bytes.
    # I bundle IMDF tipici sono 20-100MB; entrano in RAM senza problemi.
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in local_dir.rglob("*"):
            if p.is_file():
                arcname = p.relative_to(local_dir)
                zf.write(p, arcname=str(arcname))
    zip_bytes = zip_buf.getvalue()

    zk = _zip_key(task_id)
    storage.upload_bytes(zip_bytes, zk)
    logger.info(
        f"[{task_id}] imdf bundle zip uploaded: {zk} ({len(zip_bytes)} bytes)"
    )

    # Meta JSON separato (se disponibile) — utile per list veloce
    mk = _meta_key(task_id)
    if meta is not None:
        meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
        storage.upload_bytes(meta_bytes, mk)
        logger.info(f"[{task_id}] imdf meta uploaded: {mk}")

    return {"zip_key": _zip_key(task_id), "meta_key": mk}


# Read (endpoint side


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

    # Cache miss: hydration RAM-first da S3 via get_object_stream.
    storage = _storage()
    zk = _zip_key(task_id)

    try:
        obj = storage.get_object_stream(zk)
    except Exception as e:
        # Tipicamente NoSuchKey dal client S3 → bundle inesistente
        logger.warning(f"[{task_id}] imdf storage hydration failed: {e}")
        raise FileNotFoundError(
            f"Bundle {task_id} non presente su storage: {e}"
        )

    # Body e' un iteratore di chunks; lo concateniamo in RAM.
    zip_bytes = b"".join(obj.get("body", []))

    local.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        # safe_extractall: zip slip + zip bomb pre-extract.
        # I bundle IMDF tipici sono ~20-100MB, ben sotto i 500MB
        # default. Override possibile via ZIP_MAX_UNCOMPRESSED_BYTES
        # se emergono bundle "grandi venue" (aeroporti).
        safe_extractall(zf, local)

    logger.info(f"[{task_id}] imdf bundle hydrated from storage -> {local}")
    return local


def get_meta_only(task_id: str) -> Optional[JsonObject]:
    """Legge SOLO `plenora_metadata.json` da S3 (senza download zip).

    Usato dal list endpoint per enumerare bundle senza costi di estrazione.
    Ritorna None se il meta non esiste su S3 (bundle non ancora pubblicato
    o solo local-cached) oppure se la capability object storage e' off.
    """
    if not object_storage_enabled():
        return None
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
    quelli cached sul nodo corrente). ritorna [] se la
    capability object storage e' disabilitata (router fallback su
    cache locale only)."""
    if not object_storage_enabled():
        return []
    storage = _storage()
    objs = storage.list_objects(f"{PREFIX}/")
    task_ids = []
    for o in objs:
        key = o["key"]
        # Filtra solo i meta.json per dedup (evita doppio-conteggio zip+meta
        if key.endswith(META_SUFFIX):
            # Estrai task_id: "imdf-bundles/task-abc.meta.json" -> "task-abc"
            basename = key[len(PREFIX) + 1:]  # strip "imdf-bundles/"
            task_id = basename[:-len(META_SUFFIX)]
            task_ids.append(task_id)
    return task_ids


def delete_bundle(task_id: str, *, cleanup_local: bool = True) -> int:
    """Cancella zip + meta da S3. Ritorna count oggetti eliminati."""
    storage = _storage()
    deleted = 0
    for key in (_zip_key(task_id), _meta_key(task_id)):
        try:
            storage.delete(key)
            deleted += 1
        except Exception as e:
            logger.warning(f"delete {key} fallito (non-fatal): {e}")

    # Cleanup cache locale sul nodo corrente (se presente
    if cleanup_local:
        local = _local_dir(task_id)
        if local.exists():
            rmtree_safe(local, logger=logger)

    return deleted
