"""Temporary path helpers for result persistence and path-only adapters."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path


PATH_ONLY_ADAPTER_PREFIX = "plenora-path-only-adapter-"
RESULT_STREAM_TEMP_PREFIX = "plenora-result-stream-"
RESULT_CACHE_TEMP_PREFIX = "plenora-result-cache-"


def _settings_temp_dir() -> Path:
    from app.config import settings

    temp_dir = Path(settings.TEMP_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _normalize_suffix(suffix: str | None) -> str:
    if not suffix:
        return ""
    suffix = Path(str(suffix).replace("\\", "/")).suffix or str(suffix)
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", suffix)


def make_path_only_adapter_dir(
    *,
    prefix: str = PATH_ONLY_ADAPTER_PREFIX,
    temp_dir: Path | None = None,
) -> Path:
    """Create a short-lived directory for APIs that require a filesystem path.

    The directory is adapter state only: callers must persist bytes/object-storage
    records separately and remove this path when their path-only library returns.
    """
    root = Path(temp_dir) if temp_dir is not None else _settings_temp_dir()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


def write_path_only_adapter_bytes(
    data: bytes,
    *,
    suffix: str | None = None,
    prefix: str = PATH_ONLY_ADAPTER_PREFIX,
    temp_dir: Path | None = None,
) -> Path:
    """Materialize bytes under ``settings.TEMP_DIR`` for path-only libraries.

    This helper is for adapter bounce files only. It does not create canonical
    local state; callers own cleanup via :func:`cleanup_path_only_adapter`.
    """
    root = Path(temp_dir) if temp_dir is not None else _settings_temp_dir()
    root.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=_normalize_suffix(suffix),
        prefix=prefix,
        dir=root,
    )
    path = Path(tmp.name)
    try:
        tmp.write(data)
        tmp.close()
    except Exception:
        try:
            tmp.close()
        except Exception:
            pass
        cleanup_path_only_adapter(path, prefix=prefix, temp_dir=root)
        raise
    return path


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def cleanup_path_only_adapter(
    path: str | Path,
    *,
    prefix: str = PATH_ONLY_ADAPTER_PREFIX,
    temp_dir: Path | None = None,
) -> bool:
    """Best-effort cleanup for files/dirs created by this adapter policy."""
    target = Path(path)
    root = Path(temp_dir) if temp_dir is not None else _settings_temp_dir()
    if not target.name.startswith(prefix) or not _is_under(target, root):
        return False
    try:
        if target.is_dir():
            shutil.rmtree(target)
            return True
        if target.exists():
            target.unlink()
            return True
    except OSError:
        return False
    return False


def get_temp_output_path(filename: str) -> Path:
    """Return a unique adapter path under TEMP_DIR for streamed result writers."""
    tmp_dir = make_path_only_adapter_dir(prefix=RESULT_STREAM_TEMP_PREFIX)
    safe_name = Path(str(filename).replace("\\", "/")).name or "result"
    return tmp_dir / safe_name


def make_cached_result_path(suffix: str) -> Path:
    """Return a temp file path for legacy callers that need a materialized result."""
    return _settings_temp_dir() / (
        f"{RESULT_CACHE_TEMP_PREFIX}{os.urandom(4).hex()}{_normalize_suffix(suffix)}"
    )


__all__ = [
    "PATH_ONLY_ADAPTER_PREFIX",
    "RESULT_CACHE_TEMP_PREFIX",
    "RESULT_STREAM_TEMP_PREFIX",
    "cleanup_path_only_adapter",
    "get_temp_output_path",
    "make_path_only_adapter_dir",
    "make_cached_result_path",
    "write_path_only_adapter_bytes",
]
