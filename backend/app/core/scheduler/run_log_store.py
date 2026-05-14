"""DB reader boundary for scheduled-run catalog entries."""

from __future__ import annotations

from typing import Any

from psycopg2 import Error as PsycopgError

from app.core.scheduler import db as scheduler_db
from app.utils.logger import get_logger


logger = get_logger(__name__)


class ScheduleRunLogStore:
    """Read-only schedule log boundary consumed by ScheduleRunsCatalog."""

    def list_completed_runs(self, schedule_id: str) -> list[dict[str, Any]]:
        try:
            return scheduler_db.list_schedule_logs(
                schedule_id=schedule_id,
                events=("PIPELINE_COMPLETED", "PIPELINE_PARTIAL"),
            )
        except (PsycopgError, OSError, RuntimeError, ValueError, TypeError) as exc:
            logger.debug(
                "list_completed_runs: DB fallback skipped (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return []


__all__ = ["ScheduleRunLogStore"]
