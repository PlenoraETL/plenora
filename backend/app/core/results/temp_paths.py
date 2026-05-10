"""Temporary path helpers for result persistence."""

from __future__ import annotations

import os
from pathlib import Path


def get_temp_output_path(filename: str) -> Path:
    """Return a unique path under DATA_DIR/temp for streamed result writers."""
    from app.config import settings

    tmp_dir = Path(settings.DATA_DIR) / "temp" / f"plenora_{os.getpid()}_{os.urandom(4).hex()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / filename


def make_pipeline_fs_temp_dir(data_dir: Path) -> Path:
    """Create the short-lived directory used by filesystem-only dataframe formats."""
    temp_parent = Path(data_dir) / "temp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = temp_parent / f"urby_gpkg_shp_{os.getpid()}_{os.urandom(4).hex()}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    return tmp_dir


def make_cached_result_path(suffix: str) -> Path:
    """Return a temp file path for legacy callers that need a materialized result."""
    from app.config import settings

    tmp_dir = Path(settings.DATA_DIR) / "temp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / f"plenora_cached_{os.urandom(4).hex()}{suffix}"


__all__ = [
    "get_temp_output_path",
    "make_cached_result_path",
    "make_pipeline_fs_temp_dir",
]
