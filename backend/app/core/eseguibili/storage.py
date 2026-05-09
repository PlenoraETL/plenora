"""
Storage eseguibili su object storage cloud-agnostic.

Chiude R3 del multi-host audit: gli output degli eseguibili non devono
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from app.shared.resources.storage_layout import ESEGUIBILI_OUTPUTS_PREFIX
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Prefix condiviso con la convenzione: separato dal backup bucket pattern
# urby_*.dump) cosi' le lifecycle rule possono essere distinte. Alias
# modulare verso `app.shared.resources.storage_layout.ESEGUIBILI_OUTPUTS_PREFIX`
# BE-DATA-PLANE-001A): single source-of-truth per i prefix object
# storage
PREFIX = ESEGUIBILI_OUTPUTS_PREFIX


def _storage():
    """Lazy load della factory cloud (evita hard dep su boto3 ai test unit).

    passa per `get_runtime_object_storage`. Solleva
    `ObjectStorageDisabledError` se la capability globale e' off; il
    caller (write/upload eseguibili output) deve trattarlo come
    "feature non disponibile" (eseguibili senza storage non possono
    persistere output → fail-fast con messaggio operativo)."""
    from app.infrastructure.storage.cloud_backends import get_runtime_object_storage
    return get_runtime_object_storage()


def _key_for(task_id: str, filename: str) -> str:
    return f"{PREFIX}/{task_id}/{filename}"


def save_bytes(task_id: str, filename: str, data: bytes) -> str:
    """Upload diretto di bytes su object storage (no FS staging).

    Usato quando il produttore ha gia' i bytes in-memory (es.
    save_dataframe_to_bytes). Skippa completamente DATA_DIR/temp e
    anche /tmp del sistema: put_object(Body=bytes) boto3-style.

    Ritorna la key S3 usata.
    """
    key = _key_for(task_id, filename)
    storage = _storage()
    storage.upload_bytes(data, key)
    logger.info(f"[storage] eseguibili output uploaded (in-memory): {key}")
    return key


def save(task_id: str, src_path: Path) -> str:
    """Upload del file su object storage, poi delete del local temp.

    Ritorna la key usata (utile per audit/logging).
    """
    if not src_path.exists():
        raise FileNotFoundError(f"Source non trovato: {src_path}")
    if not src_path.is_file():
        raise ValueError(f"Source non e' un file: {src_path}")

    key = _key_for(task_id, src_path.name)
    storage = _storage()
    storage.upload(src_path, key)

    # Delete eager del local file dopo upload riuscito. Se l'upload fallisce
    # exception sopra), il file resta su disco per retry manuale
    try:
        src_path.unlink()
        # Cleanup parent directory se vuota (evita accumulo di dir orfane
        # tipo DATA_DIR/temp/urby_<pid>_<hash>/ che non servono piu'
        parent = src_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as e:
        logger.warning(f"Cleanup locale fallito per {src_path}: {e}")

    logger.info(f"[storage] eseguibili output uploaded: {key}")
    return key


def find_keys(task_id: str) -> List[str]:
    """Lista le key oggetti appartenenti a `task_id`. Oggi tipicamente 1."""
    storage = _storage()
    prefix = f"{PREFIX}/{task_id}/"
    objs = storage.list_objects(prefix)
    return [o["key"] for o in objs]


def open_stream(key: str) -> dict:
    """Apre uno stream di download. Delega al backend cloud."""
    return _storage().get_object_stream(key)


def delete_by_task(task_id: str) -> int:
    """Cancella tutti gli oggetti sotto `eseguibili-outputs/{task_id}/`.

    Ritorna count degli oggetti eliminati. Idempotente: se non c'e'
    niente, ritorna 0 senza errori.
    """
    storage = _storage()
    keys = find_keys(task_id)
    for key in keys:
        try:
            storage.delete(key)
        except Exception as e:
            logger.warning(f"[storage] delete {key} fallito (non-fatal): {e}")
    return len(keys)
