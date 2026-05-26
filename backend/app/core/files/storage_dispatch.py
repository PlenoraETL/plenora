"""Canonical file-source blob storage dispatch boundary.

Owns the policy-aware decision for persisting file-source blobs either as
database BYTEA payloads or object-storage artifacts.
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict

from app.core.io import object_storage as cloud_backends
from app.core.resources import storage_reconciliation as reconciliation
from app.shared.resources.storage_dispatch import resolve_payload_target
from app.shared.resources.storage_layout import FILE_SOURCES_PREFIX


class BlobDispatchResult(TypedDict):
    """Typed kwargs returned by `dispatch_blob_storage`.

    Shape is compatible with `db.save_file_source` and
    `db.save_file_sources_batch`.
    """

    data: Optional[bytes]
    storage_key: Optional[str]
    size_bytes: int


def dispatch_blob_storage(
    file_id: str,
    raw_data: bytes,
    filename: str,
    *,
    logger: Any,
    storage_key_id: Optional[str] = None,
) -> BlobDispatchResult:
    """Dispatch a file-source blob according to storage policy.

    Policy:
      - `db`: always return BYTEA data.
      - `object_storage`: upload and return a storage key.
      - `hybrid`: upload only when size meets the file-source threshold.

    Object-storage upload failures are re-raised so callers can avoid writing a
    DB row for an absent blob.
    """
    from app.config import settings

    size = len(raw_data)
    policy = (settings.get("STORAGE_POLICY", "hybrid") or "hybrid").strip().lower()
    threshold = int(settings.get("FILE_SOURCE_S3_THRESHOLD_BYTES"))

    use_object_storage = resolve_payload_target(
        policy=policy,
        size_bytes=size,
        threshold_bytes=threshold,
    ) == "object_storage"

    if not use_object_storage:
        return {"data": raw_data, "storage_key": None, "size_bytes": size}

    key_id = storage_key_id if storage_key_id is not None else file_id
    # Pre-fix (P2 data-layer HIGH): f-string includeva `filename` raw senza
    # normalizzazione → un filename con `..`/`/`/`\\` (es. corruzione DB,
    # caller compromesso) produceva una storage_key tipo
    # `file_sources/abc/../../../etc/passwd` che alcuni client S3 normalizzano
    # → write fuori dal prefix sources. Post-fix: `Path(filename).name`
    # taglia qualunque directory component al basename, allineato al fix
    # cluster 5 path_traversal di source_loading.
    from pathlib import Path as _Path
    safe_filename = _Path(str(filename)).name or "file.bin"
    storage_key = f"{FILE_SOURCES_PREFIX}/{key_id}/{safe_filename}"

    intent_id: Optional[str] = None
    try:
        intent_id = reconciliation.record_upload_intent(
            domain="file_source",
            storage_key=storage_key,
            reason="upload_intent_pre_dispatch",
            extras={"file_id": file_id, "size_bytes": size, "policy": policy},
        )
    except Exception as e:
        logger.warning(
            f"[FS-B1-large] upload_intent record fallito per {file_id}: {e}"
        )

    try:
        storage = cloud_backends.get_runtime_object_storage()
        storage.upload_bytes(raw_data, storage_key)
    except Exception as e:
        logger.error(
            f"[FS-B1-large] Upload object storage fallito per {file_id} "
            f"(policy={policy}, {size // (1024 * 1024)} MB): {e}"
        )
        # Pre-fix `mark_done` marcava l'intent come "completato con
        # successo" anche su upload fallito → il riconciliator perdeva
        # traccia dell'orphan e l'invariante "ogni intent pending
        # converge a done o failed" era violata silenziosamente.
        # Fix: registrare il fallimento con `mark_failed_attempt` che
        # incrementa il contatore retry e setta last_error; il
        # reconciler-loop decidera' poi la transizione a `lost` dopo
        # MAX_RETRIES.
        if intent_id is not None:
            try:
                reconciliation.mark_failed_attempt(
                    intent_id,
                    error=f"upload_bytes failed: {type(e).__name__}: {e}",
                )
            except Exception as mark_err:
                logger.warning(
                    f"[FS-B1-large] upload_intent mark_failed fallito per "
                    f"{file_id}: {mark_err}"
                )
        raise

    logger.info(
        f"[FS-B1-large] File source {file_id} su object storage "
        f"(policy={policy}, {size // (1024 * 1024)} MB): {storage_key}"
    )
    return {"data": None, "storage_key": storage_key, "size_bytes": size}
