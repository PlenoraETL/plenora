"""Schedule statici del beat, celery-free (Plenora 2.0, Fase 5 F5.4).

Rimpiazza ``celery_app.conf.beat_schedule`` come sorgente delle entry
config-based del beat (quelle NON DB-driven). Gli schedule DB-driven
restano di competenza di ``_sync_from_db`` (namespace ``urby:schedule:``).
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
        # P2 I2: cleanup di file_sources temp scaduti (sweep hourly). Sempre
        # registrato: idempotente, costo trascurabile su tabella vuota.
        "cleanup-temp-files-hourly": {
            "task": "workers.tasks.cleanup_temp_files_task.run",
            "schedule": IntervalSchedule(3600.0),
            "kwargs": {},
            "options": {"queue": settings.TASK_QUEUE_CONTROL_PLANE},
        },
    }

    # Heartbeat cleanroom (opt-in di test, sub-minuto -> interval).
    # BE-CONFIG-001F: flag TEST_ONLY con default False in DEFAULTS.
    if bool(settings.get("PLENORA_CLEANROOM_BEAT_PING", False)):
        config["cleanroom-beat-ping"] = {
            "task": "cleanroom.beat_ping",
            "schedule": IntervalSchedule(5.0),
            "kwargs": {},
            "options": {},
        }

    return config


__all__ = ["beat_schedule_config"]
