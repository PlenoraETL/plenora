"""Explicit local-filesystem boundary for worker runtime operations."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


def make_runtime_temp_dir(*, prefix: str, parent: Path | None = None) -> str:
    """Create a private, process-owned temporary directory."""
    return tempfile.mkdtemp(
        prefix=prefix,
        dir=str(parent) if parent is not None else None,
    )


def write_kernel_control(path: Path, value: int) -> None:
    """Write an integer to an OS control pseudo-file (for example cgroupfs)."""
    path.write_text(str(int(value)), encoding="ascii")


def upload_local_path(storage: Any, path: Path, key: str) -> None:
    """Stream a local path through the configured object-storage provider."""
    storage.upload(path, key)


__all__ = [
    "make_runtime_temp_dir",
    "upload_local_path",
    "write_kernel_control",
]
