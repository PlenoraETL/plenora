"""Result read helpers (RAM-only).

Post-Phase5 refactor: `get_result_as_tempfile` /
`get_result_as_tempfile_cm` rimosse — nessun caller live in app/. I
consumer leggono i bytes direttamente via `get_result_as_bytes` e
delegano ai parser bytes-first (load_by_format_bytes ecc.).
"""

from __future__ import annotations

from typing import Optional

from app.core.results import store
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


def get_result_info(project_id: str) -> Optional[store.ResultMetadataPayload]:
    """Return latest result metadata without blob bytes."""
    try:
        return store.get_latest_result(project_id)
    except Exception as exc:
        logger.warning("get_result_info fallito per %s: %s", project_id, exc)
        return None


__all__ = [
    "get_result_as_bytes",
    "get_result_info",
]
