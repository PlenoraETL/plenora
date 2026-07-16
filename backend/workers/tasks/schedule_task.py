"""
Task Celery per l'esecuzione di pipeline schedulate.

Runs background execution while preserving backend runtime contracts.
"""

from datetime import datetime, timezone

from app.utils.logger import get_logger

logger = get_logger(__name__)


# Corpo task come funzione pura.
# `self` e' il self-shim dell'actor Dramatiq. Retry gestito dal PolicyEngine,
# non dal broker.
def schedule_pipeline_task(
    self,
    schedule_id: str,
    triggered_by: str = "cron",
    retry_attempt: int = 0,
    scheduled_for: str = None,
    queue_reserved: bool = False,
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
    # scheduled_for dovrebbe arrivare dal dispatcher canonico. None indica un
    # path diretto/test: fallback a now() come last-resort + warning.
    if scheduled_for is None:
        # Per i tick `cron`, scheduled_for non viene cached al sync-time:
        # lo determiniamo qui usando il tick time effettivo. Per
        # `catchup`/`manual` invece il dispatcher canonico DEVE passare
        # scheduled_for esplicito (lineage logico): un None su quei
        # path indica un bug nel dispatcher, manteniamo il warning.
        scheduled_for = datetime.now(timezone.utc).isoformat()
        if triggered_by != "cron":
            logger.warning(
                f"[GESTITO] schedule_task {schedule_id}: scheduled_for None "
                f"con triggered_by={triggered_by!r} — dispatcher non canonico? "
                f"Fallback a now()={scheduled_for}."
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
            queue_reserved=queue_reserved,
        )
        
        logger.info(f"Task schedule completato: {schedule_id} -> {result.get('status')}")
        return result
        
    except Exception as e:
        logger.error(f"[CRASH] Errore fatale task schedule {schedule_id}: {e}", exc_info=True)

        if queue_reserved:
            try:
                from app.core.scheduler.concurrency_manager import ConcurrencyManager

                ConcurrencyManager().cancel_queue_reservation(schedule_id)
            except Exception as cleanup_error:
                logger.warning(
                    f"[GESTITO] Cleanup prenotazione coda fallito per "
                    f"{schedule_id}: {cleanup_error}"
                )
        
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


# Nomi registrati degli actor/task.
schedule_pipeline_task.name = "workers.tasks.schedule_task.schedule_pipeline_task"
beat_heartbeat.name = "workers.tasks.schedule_task.beat_heartbeat"
