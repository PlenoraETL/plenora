"""Legacy result read helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.results import store
from app.core.results.temp_paths import make_cached_result_path
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_result_as_bytes(project_id: str) -> Optional[bytes]:
    """Return the latest result blob bytes for a project."""
    try:
        row = store.get_latest_result_row(project_id)
        if not row or not row.id:
            return None
        return store.get_result_data(row.id)
    except Exception as exc:
        logger.warning(f"Errore lettura blob result ({project_id}): {exc}")
        return None


def get_result_as_tempfile(project_id: str) -> Optional[Path]:
    """Materialize the latest project result into a temp file for legacy callers."""
    try:
        row = store.get_latest_result_row(project_id)
        if not row or not row.id:
            return None

        data = store.get_result_data(row.id)
        if not data:
            return None

        tmp_path = make_cached_result_path(f".{row.format or 'csv'}")
        tmp_path.write_bytes(data)
        return tmp_path
    except Exception as exc:
        logger.warning(f"Errore lettura risultato dal DB ({project_id}): {exc}")
        return None


def get_result_info(project_id: str) -> Optional[dict]:
    """Return latest result metadata without blob bytes."""
    try:
        return store.get_latest_result(project_id)
    except Exception as exc:
        logger.warning("get_result_info fallito per %s: %s", project_id, exc)
        return None


__all__ = [
    "get_result_as_bytes",
    "get_result_as_tempfile",
    "get_result_info",
]
