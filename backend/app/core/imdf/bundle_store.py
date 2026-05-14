"""IMDF bundle lifecycle boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.core.imdf import storage as imdf_storage
from app.shared.resources.path_policy import validate_imdf_task_id
from app.shared.resources.file_cleanup import rmtree_safe
from app.utils.logger import get_logger


logger = get_logger(__name__)


def resolve_bundle_dir(task_id: str, *, root: Path | None = None) -> Path:
    """Validate a bundle task id and return its local bundle directory."""
    if root is None:
        from app.config import settings

        root = settings.IMDF_BUNDLES_DIR
    return validate_imdf_task_id(task_id, root.resolve())


def ensure_local_bundle(task_id: str, *, root: Path | None = None) -> Path:
    """Return a local bundle directory, hydrating it from storage if needed."""
    bundle_dir = resolve_bundle_dir(task_id, root=root)
    if root is not None and bundle_dir.exists() and bundle_dir.is_dir():
        return bundle_dir
    try:
        return imdf_storage.ensure_local_bundle(task_id)
    except FileNotFoundError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(f"[{task_id}] hydrate storage fallita: {exc}")
        raise FileNotFoundError(f"Bundle {task_id} non trovato") from exc


def find_gdb_dir(bundle_dir: Path) -> Optional[Path]:
    """Return the first File Geodatabase directory in a bundle."""
    for entry in bundle_dir.rglob("*.gdb"):
        if entry.is_dir():
            return entry
    return None


def read_json_file(bundle_dir: Path, filename: str) -> Optional[dict[str, Any]]:
    """Read a JSON sidecar from the bundle root, returning None if absent."""
    path = bundle_dir / filename
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        logger.warning(f"read_json_file: lettura {filename} fallita: {exc}")
        return None
    return data if isinstance(data, dict) else None


def read_metadata(bundle_dir: Path) -> Optional[dict[str, Any]]:
    return read_json_file(bundle_dir, "plenora_metadata.json")


def read_remote_metadata(task_id: str) -> Optional[dict[str, Any]]:
    """Read bundle metadata from object storage without hydrating the archive."""
    try:
        data = imdf_storage.get_meta_only(task_id)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(f"read_remote_metadata: storage metadata read failed for {task_id}: {exc}")
        return None
    return data if isinstance(data, dict) else None


def list_task_ids() -> list[str]:
    """List bundle task ids known by object storage."""
    return imdf_storage.list_task_ids()


def read_renderers(bundle_dir: Path) -> Optional[dict[str, Any]]:
    return read_json_file(bundle_dir, "renderers.json")


def delete_bundle(*, task_id: str, local_dir: Path) -> int:
    """Delete one IMDF bundle from local cache and remote storage."""
    rmtree_safe(local_dir, logger=logger)
    try:
        return imdf_storage.delete_bundle(task_id, cleanup_local=False)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            f"delete_bundle: remote cleanup per '{task_id}' fallito: {exc}",
            exc_info=True,
        )
        return 0


def purge_expired_local_bundles(
    *,
    bundles_dir: Path | None = None,
    now_ts: float,
    ttl_seconds: int,
) -> int:
    """Remove expired local IMDF bundles through the bundle boundary."""
    if bundles_dir is None:
        from app.config import settings as runtime_settings

        bundles_dir = runtime_settings.IMDF_BUNDLES_DIR
    if not bundles_dir.exists():
        return 0

    removed = 0
    for entry in bundles_dir.iterdir():
        try:
            if not entry.is_dir():
                continue
            age = now_ts - entry.stat().st_mtime
            if age > ttl_seconds:
                delete_bundle(task_id=entry.name, local_dir=entry)
                removed += 1
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"purge_expired_local_bundles: errore su {entry}: {exc}")
    return removed


__all__ = [
    "delete_bundle",
    "ensure_local_bundle",
    "find_gdb_dir",
    "list_task_ids",
    "purge_expired_local_bundles",
    "read_json_file",
    "read_metadata",
    "read_remote_metadata",
    "read_renderers",
    "resolve_bundle_dir",
]
