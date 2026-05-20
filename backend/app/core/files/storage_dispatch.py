"""Canonical file-source blob storage dispatch boundary.

Owns the policy-aware decision for persisting file-source blobs either as
database BYTEA payloads or object-storage artifacts.
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict

from app.core.resources import storage_reconciliation as reconciliation
from app.infrastructure.storage import cloud_backends
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
    storage_key = f"{FILE_SOURCES_PREFIX}/{key_id}/{filename}"

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
        if intent_id is not None:
            try:
                reconciliation.mark_done(intent_id)
            except Exception as mark_err:
                logger.warning(
                    f"[FS-B1-large] upload_intent cleanup fallito per "
                    f"{file_id}: {mark_err}"
                )
        raise

    logger.info(
        f"[FS-B1-large] File source {file_id} su object storage "
        f"(policy={policy}, {size // (1024 * 1024)} MB): {storage_key}"
    )
    return {"data": None, "storage_key": storage_key, "size_bytes": size}
