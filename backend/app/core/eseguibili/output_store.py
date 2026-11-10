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
from app.shared.contracts.payloads import ObjectMap
from app.core.io.local_cleanup import rmtree_safe
from app.utils.logger import get_logger

from . import dashboard_data, storage


logger = get_logger(__name__)

OWNER_SIDECAR_FILENAME = "__owner.json"
OWNER_SIDECAR_MAX_BYTES = 16 * 1024


@dataclass(frozen=True)
class TaskOwner:
    task_id: str
    eseguibile_id: str
    user_id: str
    group_id: str
    created_at: str


def _safe_id(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in "-_")


def task_owner_path(task_id: str, *, outputs_dir: Path | None = None) -> Path:
    safe_id = _safe_id(task_id)
    root = outputs_dir or settings.ESEGUIBILI_OUTPUTS_DIR
    return root / ".owners" / f"{safe_id}.json"


def task_owner_key(task_id: str, *, group_id: str) -> str:
    safe_id = _safe_id(task_id)
    return storage._key_for(group_id, safe_id, OWNER_SIDECAR_FILENAME)


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
    iterator = iter(body)  # type: ignore[arg-type]
    try:
        return read_stream_bounded(
            (_coerce_chunk(chunk) for chunk in iterator),
            max_bytes=OWNER_SIDECAR_MAX_BYTES,
            label="eseguibili_task_owner",
            stage="eseguibili_output_store_owner",
        )
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


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
    group_id: str,
    outputs_dir: Path | None = None,
) -> None:
    owner = TaskOwner(
        task_id=task_id,
        eseguibile_id=eseguibile_id,
        user_id=user_id,
        group_id=group_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    payload = json.dumps(asdict(owner)).encode("utf-8")

    try:
        storage.save_bytes(
            task_id, OWNER_SIDECAR_FILENAME, payload, group_id=group_id,
        )
    except Exception as exc:
        logger.error(
            f"save task owner sidecar su object storage fallito per "
            f"task_id={task_id}: {exc}"
        )
        raise


def load_task_owner(
    task_id: str,
    *,
    group_id: str,
    outputs_dir: Path | None = None,
) -> Optional[Mapping[str, object]]:
    keys = [
        task_owner_key(task_id, group_id=group_id),
        f"{storage.PREFIX}/{_safe_id(task_id)}/{OWNER_SIDECAR_FILENAME}",
    ]
    for key in keys:
        try:
            stream = storage.open_stream(key, expected_group_id=group_id)
            body = stream["body"]
            try:
                payload = _read_body(body)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            return _load_owner_payload(payload)
        except Exception as exc:
            logger.debug(
                f"load task owner sidecar da object storage fallito per "
                f"task_id={task_id}: {exc}"
            )
    return None


def delete_task_owner(
    task_id: str,
    *,
    group_id: str | None = None,
    outputs_dir: Path | None = None,
    strict: bool = False,
) -> int:
    deleted = 0
    failures: list[Exception] = []
    if group_id:
        for key in (
            task_owner_key(task_id, group_id=group_id),
            f"{storage.PREFIX}/{_safe_id(task_id)}/{OWNER_SIDECAR_FILENAME}",
        ):
            try:
                storage.delete_key(key, expected_group_id=group_id)
                deleted += 1
            except Exception as exc:
                failures.append(exc)
                logger.debug(
                    f"delete task owner sidecar best-effort fallito per "
                    f"task_id={task_id}: {exc}"
                )

    try:
        path = task_owner_path(task_id, outputs_dir=outputs_dir)
        if path.exists():
            rmtree_safe(path, logger=logger)
            deleted += 1
    except Exception as exc:
        failures.append(exc)
        logger.debug(
            f"delete task owner locale best-effort fallito per "
            f"task_id={task_id}: {exc}"
        )
    if strict and failures:
        raise RuntimeError(
            f"Cleanup owner incompleto per task_id={task_id}: "
            f"{len(failures)} delete falliti"
        ) from failures[0]
    return deleted


def find_output_keys(task_id: str, *, group_id: str) -> list[str]:
    return sorted(set(
        key for key in storage.find_keys(task_id, group_id=group_id)
        if key.rsplit("/", 1)[-1] != OWNER_SIDECAR_FILENAME
        and not storage.is_internal_artifact_key(key)
    ))


def select_output_key(
    keys: list[str],
    *,
    expected_filename: str | None = None,
) -> str:
    """Select one output without silently consuming a different artifact."""
    unique_keys = sorted(set(keys))
    if expected_filename:
        safe_filename = expected_filename.replace("\\", "/").rsplit("/", 1)[-1]
        matches = [
            key for key in unique_keys
            if key.rsplit("/", 1)[-1] == safe_filename
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"Output duplicato per filename={safe_filename}")
    if len(unique_keys) == 1:
        return unique_keys[0]
    if not unique_keys:
        raise FileNotFoundError("Output eseguibile non trovato")
    raise RuntimeError(
        f"Output eseguibile ambiguo: trovati {len(unique_keys)} artifact"
    )


def open_stream(key: str, *, expected_group_id: str) -> ObjectMap:
    return storage.open_stream(key, expected_group_id=expected_group_id)


def _is_owner_key(key: str) -> bool:
    return key.rsplit("/", 1)[-1] == OWNER_SIDECAR_FILENAME


# Regola unica di cio' che il consumo del download conserva nella cartella
# del task (tutto il resto: output e artifact interni di lavoro, si toglie).
KEPT_BY_DOWNLOAD = (_is_owner_key, dashboard_data.is_dashboard_data_key)


def delete_task_outputs(
    task_id: str,
    *,
    group_id: str,
    retain_owner: bool = False,
) -> int:
    """Delete task outputs, preserving authorization metadata on failure.

    ``retain_owner=True`` e' il consumo del download (una volta): restano i
    METADATI del task (``KEPT_BY_DOWNLOAD``): il sidecar owner, che autorizza
    la successiva pulizia completa, e la copia per la dashboard Grafana, che
    resta finche' la finestra dei risultati e' aperta (anche dopo «Scarica»).
    ``retain_owner=False`` e' la pulizia completa (full-cleanup, scarto,
    pulizia richiesta): toglie tutto, owner per ultimo.
    """
    keys = sorted(set(
        key for key in storage.find_keys(task_id, group_id=group_id)
        if key.rsplit("/", 1)[-1] != OWNER_SIDECAR_FILENAME
        and not (retain_owner and any(kept(key) for kept in KEPT_BY_DOWNLOAD))
    ))
    deleted = 0
    failures: list[tuple[str, Exception]] = []
    for key in keys:
        try:
            storage.delete_key(key, expected_group_id=group_id)
            deleted += 1
        except Exception as exc:
            failures.append((key, exc))
            logger.warning(f"delete output {key} fallito: {exc}")
    if failures:
        raise RuntimeError(
            f"Cleanup output incompleto per task_id={task_id}: "
            f"{len(failures)}/{len(keys)} delete falliti"
        ) from failures[0][1]
    if not retain_owner:
        delete_task_owner(task_id, group_id=group_id, strict=True)
    return deleted


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
    "KEPT_BY_DOWNLOAD",
    "OWNER_SIDECAR_FILENAME",
    "TaskOwner",
    "delete_task_outputs",
    "select_output_key",
    "delete_task_owner",
    "find_output_keys",
    "load_task_owner",
    "open_stream",
    "purge_expired_local_outputs",
    "save_task_owner",
    "task_owner_key",
    "task_owner_path",
]
