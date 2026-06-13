"""Backup amministrativo del database (corpo task, Plenora 2.0 Fase 5).

Funzione pura ``run_manual_backup``: l'actor Dramatiq omonimo (vedi
workers/dramatiq_app.py) la invoca passando il message_id come task_id.
"""

from __future__ import annotations

import threading

from app.core.admin_backups.backup_run_store import (
    refresh_manual_backup_lock,
    release_manual_backup_lock,
)
from app.core.admin_backups.service import (
    MANUAL_LOCK_HEARTBEAT_SEC,
    MANUAL_LOCK_TTL_SEC,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _start_lock_heartbeat(owner_token: str) -> tuple[threading.Event, threading.Thread]:
    """Avvia un thread che estende periodicamente il lock backup.

    Pattern allineato a `execution_manager._start_lock_heartbeat` per
    gli scheduler_locks: TTL breve (5min) + refresh ogni 120s. Se il
    worker Celery muore (OOM/kill/disconnect), il thread si ferma con
    il processo, l'expires_at non viene piu' rinnovato, e il lock
    scade entro TTL invece di restare orfano per ore.

    Ritorna (stop_event, thread) cosi' il caller (try/finally) puo'
    fermare il thread in modo pulito a fine task.
    """
    stop_event = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop_event.wait(MANUAL_LOCK_HEARTBEAT_SEC):
            try:
                ok = refresh_manual_backup_lock(
                    owner_token=owner_token,
                    ttl_seconds=MANUAL_LOCK_TTL_SEC,
                )
                if not ok:
                    # Lock perso (es. takeover dopo TTL scaduto durante
                    # blip DB). Smetto di refreshare per evitare di
                    # rinnovare un lock di cui non sono piu' owner.
                    logger.warning(
                        "manual backup heartbeat: lock non piu' "
                        "posseduto da owner_token=%s, stop heartbeat",
                        owner_token[:8],
                    )
                    return
            except Exception:
                # Errori DB transient: lascia che il prossimo tick
                # heartbeat riprovi. Se il problema persiste, il TTL
                # scade naturalmente.
                logger.warning(
                    "manual backup heartbeat refresh fallito (retry al "
                    "prossimo tick)",
                    exc_info=True,
                )

    thread = threading.Thread(
        target=_heartbeat_loop,
        name="manual-backup-lock-heartbeat",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def run_manual_backup(
    *, owner_token: str, task_id: str | None
) -> dict[str, object]:
    """Corpo condiviso (Plenora 2.0, Fase 1) del backup manuale.

    Esegue il job di backup dentro un worker (non nel processo API),
    tenendo vivo il lock con un thread heartbeat e rilasciandolo nel
    finally. `task_id` e' iniettato dall'involucro: Celery passa
    `self.request.id`, l'actor Dramatiq passa il message_id corrente
    (vedi workers/dramatiq_app.py). Il valore finisce solo nel result
    payload — la logica non vi dipende.
    """

    stop_heartbeat, heartbeat_thread = _start_lock_heartbeat(owner_token)
    exit_code = 0
    try:
        from scripts import backup_db

        backup_db.main()
        return {
            "success": True,
            "status": "completed",
            "task_id": task_id,
        }
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if exit_code == 0:
            return {
                "success": True,
                "status": "completed",
                "task_id": task_id,
            }
        logger.error("Backup manuale fallito con exit_code=%s", exit_code)
        raise RuntimeError(f"manual_backup_failed:{exit_code}") from exc
    finally:
        # Ferma il thread heartbeat prima del release: evita una race
        # in cui un tick heartbeat rinnova il lock subito dopo che
        # release_manual_backup_lock ha eliminato la riga (riga
        # ricreata "zombie" via UPDATE WHERE owner_token = %s che
        # colpirebbe 0 righe → rowcount 0 → safe; ma meglio fermare
        # prima per chiarezza).
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)
        try:
            release_manual_backup_lock(owner_token=owner_token)
        except Exception:
            logger.warning("release lock backup manuale fallita", exc_info=True)
