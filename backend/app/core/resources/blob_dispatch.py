"""File-source blob storage dispatch boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Optional, Union
from typing_extensions import TypedDict

from app.core.io import object_storage as cloud_backends
from app.core.resources import storage_reconciliation as reconciliation
from app.shared.resources.storage_layout import FILE_SOURCES_PREFIX
from app.core.io.tenant_namespace import build_tenant_key
from app.core.io.bounded_streams import stream_size
from app.infrastructure.storage import compensation as compensation_module
from app.infrastructure.storage.compensation import CompensateOutcome


class BlobDispatchResult(TypedDict):
    """Typed kwargs returned by `dispatch_blob_storage`."""

    storage_key: str
    size_bytes: int


class UploadIntentUnavailableError(RuntimeError):
    """L'upload non puo' partire senza la relativa traccia persistente."""


def dispatch_blob_storage(
    file_id: str,
    raw_data: Union[bytes, BinaryIO],
    filename: str,
    *,
    group_id: str,
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
    storage_key = build_tenant_key(
        group_id, FILE_SOURCES_PREFIX, key_id, safe_filename,
    )

    intent_id = reconciliation.record_upload_intent(
        domain="file_source",
        storage_key=storage_key,
        reason="upload_intent_pre_dispatch",
        extras={"file_id": file_id, "size_bytes": size},
    )
    if intent_id is None:
        logger.error(
            "event=file_source_upload_intent_required "
            f"file_id={file_id} storage_key={storage_key} outcome=blocked"
        )
        raise UploadIntentUnavailableError(
            "Upload annullato: impossibile registrare la protezione "
            f"anti-orphan per file source {file_id}"
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


def finalize_blob_dispatch(
    blob_result: Mapping[str, Any],
    *,
    persisted: bool,
    file_id: str,
    logger: Any,
    cleanup_blob: Optional[Callable[[str], CompensateOutcome | None]] = None,
) -> CompensateOutcome:
    """Finalizza il lato storage dopo l'esito del write DB.

    Su commit chiude subito l'intent. Su errore DB tenta la cancellazione
    compensativa; se questa fallisce l'intent resta pending e il reconciler
    periodico conserva il percorso di recupero dopo crash o fault transienti.
    """
    storage_key = blob_result.get("storage_key")
    if not storage_key:
        return "skipped"

    key = str(storage_key)
    if persisted:
        reconciliation.mark_upload_intents_done_for_storage_key(
            domain="file_source", storage_key=key,
        )
        return "ok"

    # L'eccezione del caller non prova che il business write abbia fatto
    # rollback: save_file_source esegue anche letture post-INSERT e una
    # disconnessione dopo il commit puo' produrre un esito ambiguo. Prima di
    # cancellare verifichiamo quindi il riferimento con semantica tri-state.
    referenced = reconciliation.is_storage_key_referenced(
        key, domain="file_source",
    )
    if referenced is True:
        reconciliation.mark_upload_intents_done_for_storage_key(
            domain="file_source", storage_key=key,
        )
        return "skipped"
    if referenced is None:
        logger.warning(
            "event=file_source_upload_compensation_deferred "
            f"file_id={file_id} storage_key={key} "
            "reason=reference_check_unavailable"
        )
        return "failed"

    if cleanup_blob is None:
        outcome = compensation_module.compensating_delete(
            key,
            domain="file_source",
            reason="db_save_failed_after_object_upload",
            extras={"file_id": file_id},
            logger=logger,
        )
    else:
        callback_outcome = cleanup_blob(key)
        outcome = callback_outcome if callback_outcome in {
            "ok", "failed", "skipped",
        } else "failed"

    if outcome == "ok":
        reconciliation.mark_upload_intents_orphan_deleted_for_storage_key(
            domain="file_source", storage_key=key,
        )
    return outcome


__all__ = [
    "BlobDispatchResult",
    "UploadIntentUnavailableError",
    "dispatch_blob_storage",
    "finalize_blob_dispatch",
]
