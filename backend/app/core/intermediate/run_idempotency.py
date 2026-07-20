"""Idempotency guard for intermediate-backed async task runs."""

from app.core.intermediate import runs_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


class IdempotencyStateUnavailable(RuntimeError):
    """Raised when a side-effecting task cannot verify its run state."""


def is_already_completed(run_id: str | None) -> bool:
    """Return True only when the run is completed; state errors fail closed."""
    if not run_id:
        return False
    try:
        row = runs_db.get_run(run_id)
        if not row:
            return False
        return row.get("status") == "completed"
    except Exception as exc:
        logger.error(
            f"[IDEMPOTENCY] get_run failed for {run_id}: {exc}; "
            "execution blocked"
        )
        raise IdempotencyStateUnavailable(
            f"Stato idempotente non disponibile per run {run_id}"
        ) from exc
