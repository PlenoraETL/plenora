"""File-source blob storage dispatch boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Optional, Union
from typing_extensions import TypedDict

from app.core.io import object_storage as cloud_backends
from app.core.resources import storage_reconciliation as reconciliation
from app.shared.resources.storage_layout import FILE_SOURCES_PREFIX
from app.core.io.bounded_streams import stream_size


class BlobDispatchResult(TypedDict):
    """Typed kwargs returned by `dispatch_blob_storage`."""

    storage_key: str
    size_bytes: int


def dispatch_blob_storage(
    file_id: str,
    raw_data: Union[bytes, BinaryIO],
    filename: str,
    *,
    logger: Any,
    storage_key_id: Optional[str] = None,
    size_bytes: Optional[int] = None,
) -> BlobDispatchResult:
    """Dispatch bytes o stream; object storage non materializza il payload."""
    is_bytes = isinstance(raw_data, bytes)
    size = len(raw_data) if is_bytes else (
        int(size_bytes) if size_bytes is not None else stream_size(raw_data)
    )
    key_id = storage_key_id if storage_key_id is not None else file_id
    safe_filename = Path(str(filename)).name or "file.bin"
    storage_key = f"{FILE_SOURCES_PREFIX}/{key_id}/{safe_filename}"

    intent_id: Optional[str] = None
    try:
        intent_id = reconciliation.record_upload_intent(
            domain="file_source",
            storage_key=storage_key,
            reason="upload_intent_pre_dispatch",
            extras={"file_id": file_id, "size_bytes": size},
        )
    except Exception as e:
        logger.warning(
            f"[FS-B1-large] upload_intent record fallito per {file_id}: {e}"
        )

    upload_method = "upload_bytes" if is_bytes else "upload_fileobj"
    try:
        storage = cloud_backends.get_runtime_object_storage()
        if is_bytes:
            storage.upload_bytes(raw_data, storage_key)
        else:
            storage.upload_fileobj(raw_data, storage_key, size_bytes=size)
    except Exception as e:
        logger.error(
            f"[FS-B1-large] Upload object storage fallito per {file_id} "
            f"({size // (1024 * 1024)} MB): {e}"
        )
        if intent_id is not None:
            try:
                reconciliation.mark_failed_attempt(
                    intent_id,
                    error=f"{upload_method} failed: {type(e).__name__}: {e}",
                )
            except Exception as mark_err:
                logger.warning(
                    f"[FS-B1-large] upload_intent mark_failed fallito per "
                    f"{file_id}: {mark_err}"
                )
        raise

    logger.info(
        f"[FS-B1-large] File source {file_id} su object storage "
        f"({size // (1024 * 1024)} MB): {storage_key}"
    )
    return {"storage_key": storage_key, "size_bytes": size}


__all__ = ["BlobDispatchResult", "dispatch_blob_storage"]
