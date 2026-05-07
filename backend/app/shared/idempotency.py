"""
Idempotency guard condiviso tra i task asincroni (C4 round 3).

Motivazione
"""

from app.utils.logger import get_logger

logger = get_logger(__name__)


def is_already_completed(run_id: str) -> bool:
    """True se il run_id esiste in intermediate.runs con
    status='completed'. False in tutti gli altri casi (missing,
    running, failed, DB error, empty key)."""
    if not run_id:
        return False
    try:
        from app.core.intermediate import db as idb
        row = idb.get_run(run_id)
        if not row:
            return False
        return row.get("status") == "completed"
    except Exception as e:
        logger.warning(
            f"[IDEMPOTENCY] get_run failed for {run_id}: {e} — "
            f"fail-open, procedo con esecuzione"
        )
        return False
