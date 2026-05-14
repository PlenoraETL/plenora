"""Task-scoped output boundary for eseguibili.

Keeps output files and task ownership metadata behind one small interface so
API routes do not need to know whether the data is local-only or object-store
backed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping, Optional

from app.config import settings
from app.core.resources.bounded_reader import read_stream_bounded
from app.shared.resources.file_cleanup import rmtree_safe
from app.utils.logger import get_logger

from . import storage


logger = get_logger(__name__)

OWNER_SIDECAR_FILENAME = "__owner.json"
OWNER_SIDECAR_MAX_BYTES = 16 * 1024


@dataclass(frozen=True)
class TaskOwner:
    task_id: str
    eseguibile_id: str
    user_id: str
    created_at: str


def _safe_id(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in "-_")


def task_owner_path(task_id: str, *, outputs_dir: Path | None = None) -> Path:
    safe_id = _safe_id(task_id)
    root = outputs_dir or settings.ESEGUIBILI_OUTPUTS_DIR
    return root / ".owners" / f"{safe_id}.json"


def task_owner_key(task_id: str) -> str:
    safe_id = _safe_id(task_id)
    return f"{storage.PREFIX}/{safe_id}/{OWNER_SIDECAR_FILENAME}"


def _read_body(body: object) -> bytes:
    if hasattr(body, "read"):
        data = body.read(OWNER_SIDECAR_MAX_BYTES + 1)
        if len(data) > OWNER_SIDECAR_MAX_BYTES:
            raise ValueError("task owner metadata exceeds max size")
        return data if isinstance(data, bytes) else str(data).encode("utf-8")
    if isinstance(body, bytes):
        if len(body) > OWNER_SIDECAR_MAX_BYTES:
            raise ValueError("task owner metadata exceeds max size")
        return body
    if isinstance(body, str):
        if len(body.encode("utf-8")) > OWNER_SIDECAR_MAX_BYTES:
            raise ValueError("task owner metadata exceeds max size")
        return body.encode("utf-8")
    return read_stream_bounded(
        (_coerce_chunk(chunk) for chunk in body),  # type: ignore[arg-type]
        max_bytes=OWNER_SIDECAR_MAX_BYTES,
        label="eseguibili_task_owner",
        stage="eseguibili_output_store_owner",
    )


def _coerce_chunk(chunk: object) -> bytes:
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, str):
        return chunk.encode("utf-8")
    return bytes(chunk)


def _load_owner_payload(payload: bytes | str) -> Optional[Mapping[str, object]]:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
    except Exception as exc:
        logger.warning(
            "task owner metadata presente ma non decodificabile "
            f"({type(exc).__name__}: {exc}); fail-closed"
        )
        return None
    if not isinstance(data, Mapping):
        return None
    return data


def save_task_owner(
    task_id: str,
    eseguibile_id: str,
    user_id: str,
    *,
    outputs_dir: Path | None = None,
) -> None:
    owner = TaskOwner(
        task_id=task_id,
        eseguibile_id=eseguibile_id,
        user_id=user_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    payload = json.dumps(asdict(owner)).encode("utf-8")

    errors: list[Exception] = []
    try:
        storage.save_bytes(task_id, OWNER_SIDECAR_FILENAME, payload)
    except Exception as exc:
        errors.append(exc)
        logger.warning(
            f"save task owner sidecar su object storage fallito per "
            f"task_id={task_id}: {exc}"
        )

    try:
        path = task_owner_path(task_id, outputs_dir=outputs_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    except Exception as exc:
        errors.append(exc)
        logger.warning(
            f"save task owner locale fallito per task_id={task_id}: {exc}"
        )

    if len(errors) == 2:
        raise errors[-1]


def load_task_owner(
    task_id: str,
    *,
    outputs_dir: Path | None = None,
) -> Optional[Mapping[str, object]]:
    key = task_owner_key(task_id)
    try:
        stream = storage.open_stream(key)
        return _load_owner_payload(_read_body(stream["body"]))
    except Exception as exc:
        logger.debug(
            f"load task owner sidecar da object storage fallito per "
            f"task_id={task_id}: {exc}"
        )

    path = task_owner_path(task_id, outputs_dir=outputs_dir)
    if not path.exists():
        return None
    try:
        return _load_owner_payload(path.read_bytes())
    except Exception as exc:
        logger.warning(
            f"load task owner locale fallito per task_id={task_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def delete_task_owner(task_id: str, *, outputs_dir: Path | None = None) -> None:
    try:
        storage.delete_key(task_owner_key(task_id))
    except Exception as exc:
        logger.debug(
            f"delete task owner sidecar best-effort fallito per "
            f"task_id={task_id}: {exc}"
        )

    try:
        path = task_owner_path(task_id, outputs_dir=outputs_dir)
        if path.exists():
            path.unlink()
    except Exception as exc:
        logger.debug(
            f"delete task owner locale best-effort fallito per "
            f"task_id={task_id}: {exc}"
        )


def find_output_keys(task_id: str) -> list[str]:
    return [
        key for key in storage.find_keys(task_id)
        if key.rsplit("/", 1)[-1] != OWNER_SIDECAR_FILENAME
    ]


def open_stream(key: str) -> dict:
    return storage.open_stream(key)


def delete_task_outputs(task_id: str) -> int:
    try:
        return storage.delete_by_task(task_id)
    finally:
        delete_task_owner(task_id)


def purge_expired_local_outputs(
    *,
    outputs_dir: Path | None = None,
    now_ts: float,
    ttl_seconds: int,
) -> int:
    """Remove expired local eseguibili output directories."""
    if outputs_dir is None:
        from app.config import settings as runtime_settings

        root = runtime_settings.ESEGUIBILI_OUTPUTS_DIR
    else:
        root = outputs_dir
    if not root.exists():
        return 0

    removed = 0
    for entry in root.iterdir():
        try:
            if not entry.is_dir() or entry.name == ".owners":
                continue
            age = now_ts - entry.stat().st_mtime
            if age > ttl_seconds:
                rmtree_safe(entry, logger=logger)
                delete_task_owner(entry.name, outputs_dir=root)
                removed += 1
        except Exception as exc:
            logger.warning(f"purge_expired_local_outputs: errore su {entry}: {exc}")
    return removed


__all__ = [
    "OWNER_SIDECAR_FILENAME",
    "TaskOwner",
    "delete_task_outputs",
    "delete_task_owner",
    "find_output_keys",
    "load_task_owner",
    "open_stream",
    "purge_expired_local_outputs",
    "save_task_owner",
    "task_owner_key",
    "task_owner_path",
]
