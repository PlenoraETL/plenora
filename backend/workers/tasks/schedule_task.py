"""
Task Celery per l'esecuzione di pipeline schedulate.

Runs background execution while preserving backend runtime contracts.
"""

from datetime import datetime, timezone

from workers.celery_app import celery_app
from app.utils.logger import get_logger
from app.config import settings

logger = get_logger(__name__)


@celery_app.task(
    name="workers.tasks.schedule_task.schedule_pipeline_task",
    bind=True,
    max_retries=0,          # Retry gestito dal PolicyEngine, non da Celery
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.CELERY_SCHEDULE_TIMEOUT,
    time_limit=settings.CELERY_SCHEDULE_HARD_LIMIT,
)
def schedule_pipeline_task(
    self,
    schedule_id: str,
    triggered_by: str = "cron",
    retry_attempt: int = 0,
    scheduled_for: str = None,
):
    """
    Task Celery per eseguire una pipeline schedulata.

    Chiamato da:
    - Celery Beat (cron) → triggered_by="cron"
    - API "Esegui Ora" → triggered_by="manual"
    - PolicyEngine (retry) → triggered_by originale, retry_attempt > 0
    - Beat catchup → triggered_by="catchup" + scheduled_for

    Args:
        schedule_id: ID dello schedule da eseguire
        triggered_by: Chi ha triggerato ("cron", "manual", "catchup")
        retry_attempt: Numero tentativo corrente (0 = primo)
        scheduled_for: ISO8601 del "tick logico" recuperato. Valorizzato
            solo per triggered_by='catchup'. Rappresenta il momento cron
            previsto per cui stiamo recuperando il run (es. "2026-04-16
            06:00" quando il sistema era down alle 06:00 e il catchup
            scatta alle 10:00). Passato a ExecutionManager per lineage.
            None per run normali (cron/manual/retry).

    Returns:
        Dict con risultato esecuzione
    """
    # `scheduled_for` viene SEMPRE valorizzato a dispatch-time da
    # `job_dispatcher.scheduled_run_kwargs` (commit 2026-05-21). Ricevere
    # None qui significa che il task e' stato dispatched bypassando il
    # dispatcher canonico (path legacy o test diretti): fallback a now()
    # come last-resort + warning per visibilita' al monitoring.
    #
    # Pre-fix questo blocco veniva eseguito ad ogni invocazione del task
    # (anche su retry-celery dello stesso schedule_id) generando un
    # nuovo timestamp → la chiave idempotency CARICA
    # `schedule-{id}-{scheduled_for}-carica` cambiava → guard
    # idempotency non scattava → scrittura duplicata sul DB di
    # destinazione.
    if scheduled_for is None:
        scheduled_for = datetime.now(timezone.utc).isoformat()
        logger.warning(
            f"[GESTITO] schedule_task {schedule_id}: scheduled_for None — "
            f"dispatcher non canonico? Fallback a now()={scheduled_for}."
        )

    logger.info(
        f"Task schedule avviato: {schedule_id} "
        f"(triggered_by={triggered_by}, attempt={retry_attempt}, "
        f"scheduled_for={scheduled_for})"
    )

    try:
        from app.core.scheduler.execution_manager import ExecutionManager

        manager = ExecutionManager()
        result = manager.execute(
            schedule_id=schedule_id,
            triggered_by=triggered_by,
            retry_attempt=retry_attempt,
            scheduled_for=scheduled_for,
        )
        
        logger.info(f"Task schedule completato: {schedule_id} -> {result.get('status')}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore fatale task schedule {schedule_id}: {e}", exc_info=True)
        
        # Log di emergenza — se l'ExecutionManager stesso è crashato
        try:
            from app.core.scheduler.log_service import ScheduleLogService
            log_service = ScheduleLogService()
            entry = log_service.create_log_entry(
                schedule_id=schedule_id,
                event="PIPELINE_FAILED",
                triggered_by=triggered_by,
                level="ERROR",
                error_message=f"Errore fatale nel task Celery: {str(e)}",
                execution_info={"retry_attempt": retry_attempt}
            )
            log_service.write_log(entry)
        except Exception as e:
            logger.debug(f"log emergenza post-crash fallito: {type(e).__name__}: {e}")
        
        # intent: boundary top-level task Celery / outcome: fail-closed sanitizzato
        # Il dettaglio tecnico resta solo nel logger.error([CRASH]) + nel log_entry
        # schedule_logs (canale admin diagnostics). Il return dict finisce in Redis
        # via AsyncResult e viene esposto via get_task_status al FE: niente str(e
        return {
            "success": False,
            "status": "failed",
            "error": "schedule_task_fatal",
            "message": "Errore fatale del task schedule (consulta log)",
        }


@celery_app.task(
    name="workers.tasks.schedule_task.beat_heartbeat",
    max_retries=0,
    ignore_result=True,
)
def beat_heartbeat():
    """
    Heartbeat periodico scritto da Beat ogni 60 secondi.
    Scrive timestamp nel DB PostgreSQL (usato dall'healthcheck).
    """
    try:
        from app.db.pg_connection import unit_of_work
        now = datetime.now(timezone.utc).isoformat()
        with unit_of_work() as session:
            session.execute(
                "INSERT INTO beat_heartbeat (id, ts) VALUES ('beat', %s) "
                "ON CONFLICT (id) DO UPDATE SET ts = EXCLUDED.ts",
                (now,)
            )
    except Exception as e:
        # Breadcrumb obbligatorio: la tabella beat_heartbeat e' letta
        # dall'healthcheck — un silent swallow qui nasconde un DB down
        # dietro un beat apparentemente sano. Non re-raise: il task e'
        # `ignore_result=True` e non deve rompere il tick beat
        logger.warning(
            f"[HEARTBEAT] INSERT beat_heartbeat fallito "
            f"(healthcheck vedra' beat stale): {e}"
        )
