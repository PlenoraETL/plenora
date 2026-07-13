"""Object-storage data plane for asynchronous pipeline previews.

Task results stored in PostgreSQL must stay a small control-plane envelope.
The tabular preview payload (columns + rows) is therefore serialized to the
runtime S3-compatible store and hydrated only at the authenticated HTTP
boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from tempfile import SpooledTemporaryFile
from typing import Mapping, Optional

from app.core.io import object_storage as object_storage_module
from app.shared.resources.storage_layout import PREVIEWS_PREFIX
from app.utils.logger import get_logger
from app.core.io.bounded_streams import spool_memory_limit, stream_size

logger = get_logger(__name__)

PREVIEW_STORAGE_KEY_FIELD = "preview_storage_key"


class PreviewPayloadError(RuntimeError):
    """Raised when a preview payload cannot be persisted or hydrated."""


def _storage_key(project_id: str, task_id: str) -> str:
    safe_project = "".join(c for c in str(project_id) if c.isalnum() or c in "-_")
    safe_task = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")
    if not safe_project or safe_project != str(project_id):
        raise PreviewPayloadError("project_id non valido per preview storage")
    if not safe_task or safe_task != str(task_id):
        raise PreviewPayloadError("task_id non valido per preview storage")
    return f"{PREVIEWS_PREFIX}/{safe_project}/{safe_task}.json"


def persist_preview_payload(
    payload: Mapping[str, object],
    *,
    project_id: str,
    task_id: str,
) -> dict[str, object]:
    """Persist a successful preview and return its small task envelope."""
    key = _storage_key(project_id, task_id)
    spool = SpooledTemporaryFile(max_size=spool_memory_limit(), mode="w+b")
    try:
        text_stream = io.TextIOWrapper(spool, encoding="utf-8", write_through=True)
        json.dump(
            dict(payload),
            text_stream,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        text_stream.flush()
        text_stream.detach()
        size_bytes = stream_size(spool)
        object_storage_module.get_runtime_object_storage().upload_fileobj(
            spool,
            key,
            size_bytes=size_bytes,
        )
    except Exception as exc:
        raise PreviewPayloadError(
            f"Salvataggio preview su object storage fallito: {type(exc).__name__}"
        ) from exc
    finally:
        spool.close()

    return {
        "success": True,
        "project_id": project_id,
        "task_id": task_id,
        PREVIEW_STORAGE_KEY_FIELD: key,
        "preview_size_bytes": size_bytes,
        "preview_count": payload.get("preview_count"),
        "total_count": payload.get("total_count"),
        "truncated": payload.get("truncated"),
    }


def is_preview_reference(payload: object) -> bool:
    return isinstance(payload, Mapping) and bool(
        payload.get(PREVIEW_STORAGE_KEY_FIELD)
    )


def hydrate_preview_payload(
    payload: Mapping[str, object],
    *,
    max_bytes: Optional[int] = None,
) -> dict[str, object]:
    """Read a preview reference from S3 and return the original JSON object."""
    key = str(payload.get(PREVIEW_STORAGE_KEY_FIELD) or "")
    if not key.startswith(f"{PREVIEWS_PREFIX}/"):
        raise PreviewPayloadError("Riferimento preview object storage non valido")

    if max_bytes is None:
        from app.config import settings

        max_bytes = int(settings.get("PREVIEW_PAYLOAD_MAX_BYTES", 50 * 1024 * 1024))

    spool = None
    try:
        stream = object_storage_module.get_runtime_object_storage().get_object_stream(key)
        declared_size = int(stream.get("size") or 0)
        if max_bytes > 0 and declared_size > max_bytes:
            raise PreviewPayloadError(
                f"Preview {declared_size} byte supera PREVIEW_PAYLOAD_MAX_BYTES={max_bytes}"
            )
        spool = SpooledTemporaryFile(max_size=spool_memory_limit(), mode="w+b")
        downloaded_size = 0
        for chunk in stream["body"]:
            downloaded_size += len(chunk)
            if max_bytes > 0 and downloaded_size > max_bytes:
                raise PreviewPayloadError(
                    f"Preview supera PREVIEW_PAYLOAD_MAX_BYTES={max_bytes}"
                )
            spool.write(chunk)
        spool.seek(0)
        text_stream = io.TextIOWrapper(spool, encoding="utf-8")
        try:
            decoded = json.load(text_stream)
        finally:
            text_stream.detach()
            spool.close()
    except PreviewPayloadError:
        raise
    except Exception as exc:
        raise PreviewPayloadError(
            f"Lettura preview da object storage fallita: {type(exc).__name__}"
        ) from exc
    finally:
        if spool is not None and not spool.closed:
            spool.close()
    if not isinstance(decoded, dict):
        raise PreviewPayloadError("Payload preview object storage non valido")
    return decoded


def delete_preview_payload(payload: Mapping[str, object]) -> bool:
    """Best-effort delete of the object referenced by a task envelope."""
    key = str(payload.get(PREVIEW_STORAGE_KEY_FIELD) or "")
    if not key.startswith(f"{PREVIEWS_PREFIX}/"):
        return False
    try:
        object_storage_module.get_runtime_object_storage().delete(key)
        return True
    except Exception:
        logger.warning("cleanup preview object fallito key=%s", key, exc_info=True)
        return False


def cleanup_expired_preview_payloads(*, ttl_seconds: int) -> int:
    """Delete preview objects older than the task-result TTL.

    Provider-side lifecycle remains the crash-safety net; this sweep keeps
    behavior uniform on S3-compatible providers with partial lifecycle APIs.
    """
    if ttl_seconds <= 0:
        return 0
    storage = object_storage_module.get_runtime_object_storage()
    now = datetime.now(timezone.utc)
    removed = 0
    for obj in storage.list_objects(f"{PREVIEWS_PREFIX}/"):
        modified = obj.get("last_modified")
        if modified is None:
            continue
        if getattr(modified, "tzinfo", None) is None:
            modified = modified.replace(tzinfo=timezone.utc)
        if (now - modified).total_seconds() < ttl_seconds:
            continue
        storage.delete(str(obj["key"]))
        removed += 1
    return removed


__all__ = [
    "PREVIEW_STORAGE_KEY_FIELD",
    "PreviewPayloadError",
    "cleanup_expired_preview_payloads",
    "delete_preview_payload",
    "hydrate_preview_payload",
    "is_preview_reference",
    "persist_preview_payload",
]
