"""Idempotency guard for intermediate-backed async task runs."""

from app.core.intermediate import runs_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


def is_already_completed(run_id: str | None) -> bool:
    """Return True only when the intermediate run exists and is completed."""
    if not run_id:
        return False
    try:
        row = runs_db.get_run(run_id)
        if not row:
            return False
        return row.get("status") == "completed"
    except Exception as exc:
        logger.warning(
            f"[IDEMPOTENCY] get_run failed for {run_id}: {exc}; "
            f"fail-open and continue execution"
        )
        return False
