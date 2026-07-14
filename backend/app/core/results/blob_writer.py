"""Write result blobs to the configured result storage backend."""

from __future__ import annotations

from typing import Optional
import uuid

from app.core.io import object_storage as object_storage_module
from app.core.io.tenant_namespace import build_tenant_key
from app.core.resources import storage_reconciliation as reconciliation_module
from app.core.results import store
from app.core.results.retention import apply_results_retention
from app.infrastructure.storage import compensation as compensation_module
from app.infrastructure.storage.compensation import CompensateOutcome
from app.shared.resources.storage_layout import RESULTS_PREFIX
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ResultSaveError(Exception):
    """Fatal error while saving a pipeline result."""


def cleanup_uploaded_result_blob(
    storage,
    storage_key: str,
    *,
    reason: str,
    project_id: str,
    result_id: str,
) -> CompensateOutcome:
    """Best-effort delete for an object blob whose DB row failed to persist."""
    return compensation_module.compensating_delete(
        storage_key,
        domain="result",
        reason=reason,
        storage=storage,
        extras={"project_id": project_id, "result_id": result_id},
        logger=logger,
    )


def record_result_upload_intent(
    storage_key: str,
    *,
    project_id: str,
    result_id: str,
    filename: str,
    size_bytes: int,
) -> Optional[str]:
    """Record a pre-upload intent for result object-storage blobs."""
    return reconciliation_module.record_upload_intent(
        domain="result",
        storage_key=storage_key,
        reason="result_upload_intent_pre_db_row",
        extras={
            "project_id": project_id,
            "result_id": result_id,
            "filename": filename,
            "size_bytes": size_bytes,
        },
    )


def close_result_upload_intent(intent_id: Optional[str]) -> None:
    if not intent_id:
        return

    reconciliation_module.mark_done(intent_id)


def close_failed_result_upload_intent(
    intent_id: Optional[str],
    *,
    cleanup_outcome: CompensateOutcome,
) -> None:
    if not intent_id:
        return
    if cleanup_outcome == "ok":
        reconciliation_module.mark_orphan_deleted(intent_id)


def _storage_key(group_id: str, project_id: str, result_id: str) -> str:
    return build_tenant_key(
        group_id, RESULTS_PREFIX, project_id, f"{result_id}.bin",
    )


def _runtime_storage_upload_error(exc: Exception, *, result_id: str, filename: str | None = None) -> ResultSaveError:
    hint = object_storage_module.aws_credentials_error_message(exc)
    if hint is not None:
        suffix = f", file {filename}" if filename else ""
        return ResultSaveError(f"{hint} (result {result_id}{suffix})")
    if filename:
        return ResultSaveError(
            f"Upload object storage fallito per result {result_id} ({filename}): {exc}"
        )
    return ResultSaveError(f"Upload object storage fallito per result {result_id}: {exc}")


# Query streaming output usa `StreamingResultBlobWriter` (S3 multipart
# real-time o DB BYTEA buffer), evitando entry point path-based.
# orchestrato da `persist_streaming_query_to_storage`: niente piu'
# tempfile intermedio fra cursor query e storage canonical.
# Per source in-memory continua a valere `save_result_blob_from_bytes`.


def save_result_blob_from_bytes(
    project_id: str,
    data: bytes,
    filename: str,
    fmt: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
) -> str:
    """Persist result bytes in object storage and only metadata in PostgreSQL."""
    try:
        size = len(data)
        resolved_group_id = store.resolve_group_id_for_result(project_id, group_id)
        result_id = str(uuid.uuid4())

        storage_key = _storage_key(resolved_group_id, project_id, result_id)
        upload_intent_id: Optional[str] = None
        try:
            storage = object_storage_module.get_runtime_object_storage()
            upload_intent_id = record_result_upload_intent(
                storage_key,
                project_id=project_id,
                result_id=result_id,
                filename=filename,
                size_bytes=size,
            )
            storage.upload_bytes(data, storage_key)
        except Exception as exc:
            raise _runtime_storage_upload_error(exc, result_id=result_id) from exc

        try:
            store.save_result_row(
                result_id=result_id,
                project_id=project_id,
                group_id=resolved_group_id,
                filename=filename,
                fmt=fmt,
                rows_count=rows_count,
                storage_key=storage_key,
                size_bytes=size,
            )
            close_result_upload_intent(upload_intent_id)
        except Exception:
            cleanup_outcome = cleanup_uploaded_result_blob(
                storage,
                storage_key,
                reason="db_save_failed_after_object_upload",
                project_id=project_id,
                result_id=result_id,
            )
            close_failed_result_upload_intent(
                upload_intent_id,
                cleanup_outcome=cleanup_outcome,
            )
            raise
        logger.info(
            f"[FS-4] Result {result_id} su object storage "
            f"({size // (1024 * 1024)} MB): {storage_key}"
        )

        apply_results_retention(project_id)
        return result_id
    except ResultSaveError:
        raise
    except Exception as exc:
        logger.error(
            f"[CRASH] save_result_blob_from_bytes fallito per project_id={project_id} "
            f"filename={filename}: {exc}",
            exc_info=True,
        )
        raise ResultSaveError(
            f"Salvataggio risultato in-memory fallito per {project_id}: {exc}"
        ) from exc


__all__ = [
    "ResultSaveError",
    "cleanup_uploaded_result_blob",
    "close_failed_result_upload_intent",
    "close_result_upload_intent",
    "record_result_upload_intent",
    "save_result_blob_from_bytes",
]
