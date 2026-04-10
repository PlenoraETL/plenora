"""
Task Celery per l'esecuzione di pipeline schedulate.

Architettura
============
Questo task è il punto di ingresso dal mondo Celery/Beat al sistema scheduler.
Celery Beat (via RedBeat) triggera schedule_pipeline_task ad ogni cron tick.

Il task è volutamente snello: delega tutta la logica all'ExecutionManager
che gestisce lock, pre-check, esecuzione, retry, log.

Flusso:
    Beat (cron) → schedule_pipeline_task → ExecutionManager.execute()
    
    UI ("Esegui Ora") → API → schedule_pipeline_task.apply_async()
                              → ExecutionManager.execute(triggered_by="manual")

Registrazione:
    Aggiunto a celery_app.conf.include come "workers.tasks.schedule_task"

Avvio worker:
    celery -A workers.celery_app worker --loglevel=info --pool=solo

Avvio Beat (con RedBeat per schedule dinamici):
    celery -A workers.celery_app beat -S redbeat.RedBeatScheduler --loglevel=info
"""

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
    retry_attempt: int = 0
):
    """
    Task Celery per eseguire una pipeline schedulata.
    
    Chiamato da:
    - Celery Beat (cron) → triggered_by="cron"
    - API "Esegui Ora" → triggered_by="manual"
    - PolicyEngine (retry) → triggered_by originale, retry_attempt > 0
    
    Args:
        schedule_id: ID dello schedule da eseguire
        triggered_by: Chi ha triggerato ("cron" o "manual")
        retry_attempt: Numero tentativo corrente (0 = primo)
    
    Returns:
        Dict con risultato esecuzione
    """
    logger.info(
        f"Task schedule avviato: {schedule_id} "
        f"(triggered_by={triggered_by}, attempt={retry_attempt})"
    )
    
    try:
        from app.core.scheduler.execution_manager import ExecutionManager
        
        manager = ExecutionManager()
        result = manager.execute(
            schedule_id=schedule_id,
            triggered_by=triggered_by,
            retry_attempt=retry_attempt
        )
        
        logger.info(f"Task schedule completato: {schedule_id} → {result.get('status')}")
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
        except Exception:
            pass  # Se anche il log fallisce, non possiamo fare nulla
        
        return {
            "success": False,
            "status": "failed",
            "message": f"Errore fatale: {str(e)}"
        }


@celery_app.task(
    name="workers.tasks.schedule_task.beat_heartbeat",
    max_retries=0,
    ignore_result=True,
)
def beat_heartbeat():
    """
    Heartbeat periodico scritto da Beat ogni 60 secondi.
    Scrive timestamp nel DB SQLite (usato dall'healthcheck).
    """
    from datetime import datetime, timezone
    try:
        import app.db.db_service as db
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO beat_heartbeat (id, ts) VALUES ('beat', ?)",
            (now,)
        )
    except Exception:
        pass