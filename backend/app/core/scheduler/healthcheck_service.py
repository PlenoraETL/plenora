"""Beat healthcheck use-case for the scheduler boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.core.scheduler import db as scheduler_db
from app.core.scheduler.models import BeatHealthcheck
from app.utils.logger import get_logger


logger = get_logger(__name__)


def _as_aware_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        raise TypeError(f"expected datetime or ISO string, got {type(value).__name__}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_beat_healthcheck() -> BeatHealthcheck:
    """Return the current scheduler beat health state."""
    try:
        row = scheduler_db.get_beat_heartbeat()

        if row and row["ts"]:
            beat_time = _as_aware_datetime(row["ts"])
            age = (datetime.now(timezone.utc) - beat_time).total_seconds()
            schedules_count = scheduler_db.count_enabled_schedules()

            if age < 180:
                hard_limit = settings.TASK_SCHEDULE_HARD_LIMIT
                stuck_count = scheduler_db.count_stuck_schedules(hard_limit)
                stuck_msg = f" - {stuck_count} task bloccate" if stuck_count > 0 else ""
                beat_status = "warning" if stuck_count > 0 else "ok"
                return BeatHealthcheck(
                    status=beat_status,
                    last_beat=beat_time,
                    message=(
                        f"Beat attivo ({int(age)}s fa) - "
                        f"{schedules_count} schedule attivi{stuck_msg}"
                    ),
                )

            return BeatHealthcheck(
                status="down",
                last_beat=beat_time,
                message=f"Beat non risponde da {int(age)}s",
            )

        return BeatHealthcheck(
            status="unknown",
            message="Nessun heartbeat trovato. Attendi ~60s dopo l'avvio di Beat.",
        )

    except Exception as e:
        logger.error(f"[GESTITO] Errore healthcheck Beat: {e}", exc_info=True)
        return BeatHealthcheck(
            status="down",
            message=f"Errore: {str(e)}",
        )
