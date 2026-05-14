"""Write result blobs to the configured result storage backend."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import uuid

from app.core.results import store
from app.core.results.retention import apply_results_retention
from app.core.results.storage_policy import (
    result_db_hard_cap,
    should_use_object_storage,
    storage_policy,
)
from app.infrastructure.storage.compensation import CompensateOutcome
from app.shared.resources.storage_layout import RESULTS_PREFIX
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ResultSaveError(Exception):
    """Fatal error while saving a pipeline result."""


def apply_results_retention_after_save(project_id: str) -> None:
    """Compatibility wrapper around the results retention workflow."""
    apply_results_retention(project_id)


def cleanup_uploaded_result_blob(
    storage,
    storage_key: str,
    *,
    reason: str,
    project_id: str,
    result_id: str,
) -> CompensateOutcome:
    """Best-effort delete for an object blob whose DB row failed to persist."""
    from app.infrastructure.storage.compensation import compensating_delete

    return compensating_delete(
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
    from app.core.resources.storage_reconciliation import record_upload_intent

    return record_upload_intent(
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
    from app.core.resources.storage_reconciliation import mark_done

    mark_done(intent_id)


def close_failed_result_upload_intent(
    intent_id: Optional[str],
    *,
    cleanup_outcome: CompensateOutcome,
) -> None:
    if not intent_id:
        return
    if cleanup_outcome == "ok":
        from app.core.resources.storage_reconciliation import mark_orphan_deleted

        mark_orphan_deleted(intent_id)


def enforce_db_hard_cap(size: int, ctx: str) -> None:
    """Reject DB-backed result blobs above RESULT_DB_HARD_CAP_BYTES."""
    cap = result_db_hard_cap()
    if size > cap:
        raise ResultSaveError(
            f"[{ctx}] Payload {size} byte supera RESULT_DB_HARD_CAP_BYTES={cap}. "
            "Alza il cap, cambia STORAGE_POLICY a object_storage/hybrid, "
            "o riduci la dimensione del result."
        )


def _storage_key(project_id: str, result_id: str) -> str:
    return f"{RESULTS_PREFIX}/{project_id}/{result_id}.bin"


def _runtime_storage_upload_error(exc: Exception, *, result_id: str, filename: str | None = None) -> ResultSaveError:
    from app.infrastructure.storage.cloud_backends import aws_credentials_error_message

    hint = aws_credentials_error_message(exc)
    if hint is not None:
        suffix = f", file {filename}" if filename else ""
        return ResultSaveError(f"{hint} (result {result_id}{suffix})")
    if filename:
        return ResultSaveError(
            f"Upload object storage fallito per result {result_id} ({filename}): {exc}"
        )
    return ResultSaveError(f"Upload object storage fallito per result {result_id}: {exc}")


def save_result_blob(
    project_id: str,
    path: Path,
    fmt: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
) -> str:
    """Persist a result file as a DB blob or object-storage backed row."""
    saved = False
    try:
        resolved_group_id = store.resolve_group_id_for_result(project_id, group_id)
        result_id = str(uuid.uuid4())

        try:
            file_size = path.stat().st_size
        except OSError as exc:
            raise ResultSaveError(f"Impossibile leggere dimensione file {path}: {exc}") from exc

        policy = storage_policy()
        use_object_storage = should_use_object_storage(file_size, policy)

        if not use_object_storage:
            enforce_db_hard_cap(file_size, ctx=f"save_result_blob/{policy}")
            store.save_result_row(
                result_id=result_id,
                project_id=project_id,
                group_id=resolved_group_id,
                filename=path.name,
                fmt=fmt,
                data=path.read_bytes(),
                rows_count=rows_count,
            )
        else:
            storage_key = _storage_key(project_id, result_id)
            upload_intent_id: Optional[str] = None
            try:
                from app.infrastructure.storage.cloud_backends import get_runtime_object_storage

                storage = get_runtime_object_storage()
                upload_intent_id = record_result_upload_intent(
                    storage_key,
                    project_id=project_id,
                    result_id=result_id,
                    filename=path.name,
                    size_bytes=file_size,
                )
                storage.upload(path, storage_key)
            except Exception as exc:
                raise _runtime_storage_upload_error(
                    exc,
                    result_id=result_id,
                    filename=path.name,
                ) from exc

            try:
                store.save_result_row(
                    result_id=result_id,
                    project_id=project_id,
                    group_id=resolved_group_id,
                    filename=path.name,
                    fmt=fmt,
                    data=None,
                    rows_count=rows_count,
                    storage_key=storage_key,
                    size_bytes=file_size,
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
                f"[FS-4] Result {result_id} su object storage (file-based, "
                f"policy={policy}, {file_size // (1024 * 1024)} MB): {storage_key}"
            )

        saved = True
        apply_results_retention_after_save(project_id)
        return result_id
    except ResultSaveError:
        raise
    except Exception as exc:
        logger.error(
            f"[CRASH] save_result_blob fallito per project_id={project_id} "
            f"file={path.name}: {exc}",
            exc_info=True,
        )
        raise ResultSaveError(
            f"Salvataggio risultato nel DB fallito per {project_id}: {exc}"
        ) from exc
    finally:
        if saved:
            try:
                if path.exists():
                    path.unlink()
                if path.parent.exists() and not any(path.parent.iterdir()):
                    path.parent.rmdir()
            except Exception as exc:
                logger.warning("Cleanup file temp fallito per %s: %s", project_id, exc)


def save_result_blob_from_bytes(
    project_id: str,
    data: bytes,
    filename: str,
    fmt: str,
    rows_count: Optional[int] = None,
    group_id: Optional[str] = None,
) -> str:
    """Persist in-memory result bytes as a DB blob or object-storage backed row."""
    try:
        size = len(data)
        resolved_group_id = store.resolve_group_id_for_result(project_id, group_id)
        result_id = str(uuid.uuid4())

        policy = storage_policy()
        use_object_storage = should_use_object_storage(size, policy)

        if not use_object_storage:
            enforce_db_hard_cap(size, ctx=f"save_result_blob_from_bytes/{policy}")
            store.save_result_row(
                result_id=result_id,
                project_id=project_id,
                group_id=resolved_group_id,
                filename=filename,
                fmt=fmt,
                data=data,
                rows_count=rows_count,
            )
        else:
            storage_key = _storage_key(project_id, result_id)
            upload_intent_id: Optional[str] = None
            try:
                from app.infrastructure.storage.cloud_backends import get_runtime_object_storage

                storage = get_runtime_object_storage()
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
                    data=None,
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
                f"(policy={policy}, {size // (1024 * 1024)} MB): {storage_key}"
            )

        apply_results_retention_after_save(project_id)
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
    "apply_results_retention_after_save",
    "cleanup_uploaded_result_blob",
    "close_failed_result_upload_intent",
    "close_result_upload_intent",
    "enforce_db_hard_cap",
    "record_result_upload_intent",
    "save_result_blob",
    "save_result_blob_from_bytes",
]
