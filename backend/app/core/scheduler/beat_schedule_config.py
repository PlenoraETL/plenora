"""Schedule statici del beat celery-free.

Sorgente delle entry config-based del beat (quelle NON DB-driven).
Gli schedule DB-driven restano di competenza di ``_sync_from_db``
(namespace ``urby:schedule:``).
"""

from __future__ import annotations

from typing import Any, Mapping

from app.core.scheduler.cron_schedule import IntervalSchedule


def beat_schedule_config() -> Mapping[str, Mapping[str, Any]]:
    """Entry config-based del beat (interval-based), celery-free.

    Ogni entry: ``{task, schedule (Schedule), kwargs, options}``. Ritorno
    tipato ``Mapping`` (non ``dict`` broad) per il typed-boundary guard.
    """
    from app.config import settings

    config: dict[str, dict[str, Any]] = {
        # Cleanup di file_sources temp scaduti: idempotente, costo
        # trascurabile su tabella vuota.
        "cleanup-temp-files-hourly": {
            "task": "workers.tasks.cleanup_temp_files_task.run",
            "schedule": IntervalSchedule(3600.0),
            "kwargs": {},
            "options": {"queue": settings.TASK_QUEUE_CONTROL_PLANE},
        },
        # Pulizie complete in coda dei task ormai terminali (UI conferma
        # finale F8/G3, app/core/orchestrator/task_cleanup): l'esecutore che
        # chiude la pagina con un task in corso non lascia tracce.
        "task-cleanup-deferred-minutely": {
            "task": "workers.tasks.task_cleanup_task.run",
            "schedule": IntervalSchedule(60.0),
            "kwargs": {},
            "options": {"queue": settings.TASK_QUEUE_CONTROL_PLANE},
        },
    }

    # Heartbeat cleanroom: opt-in di test, sub-minuto -> interval.
    if bool(settings.get("PLENORA_CLEANROOM_BEAT_PING", False)):
        config["cleanroom-beat-ping"] = {
            "task": "cleanroom.beat_ping",
            "schedule": IntervalSchedule(5.0),
            "kwargs": {},
            "options": {},
        }

    return config


__all__ = ["beat_schedule_config"]
