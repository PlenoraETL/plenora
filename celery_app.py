"""
Celery Application Configuration

Configurazione centralizzata per task asincroni.

AVVIO WORKER:
    celery -A celery_app worker --loglevel=info --concurrency=4

AVVIO BEAT (scheduler):
    celery -A celery_app beat --loglevel=info

MONITORAGGIO:
    celery -A celery_app flower  (richiede pip install flower)
"""

from celery import Celery
from config import settings

# Crea istanza Celery
celery_app = Celery(
    "dataflow_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "tasks.enrichment_task",
    ]
)

# Configurazione Celery
celery_app.conf.update(
    # Serializzazione
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # Timezone
    timezone="Europe/Rome",
    enable_utc=True,
    
    # Risultati
    result_expires=settings.CELERY_RESULT_EXPIRES,
    result_extended=True,  # Include task_id, status, etc.
    
    # Task tracking
    task_track_started=True,
    task_send_sent_event=True,
    
    # Worker
    worker_prefetch_multiplier=1,  # Un task alla volta per worker
    worker_concurrency=4,
    
    # Retry policy
    task_acks_late=True,  # Conferma solo dopo completamento
    task_reject_on_worker_lost=True,
    
    # Timeout default
    task_soft_time_limit=settings.CELERY_TASK_TIMEOUT - 60,  # Soft limit 1 min prima
    task_time_limit=settings.CELERY_TASK_TIMEOUT,
    
    # Rate limiting (opzionale)
    task_default_rate_limit="100/m",  # 100 task/minuto default
    
    # Beat schedule (per task schedulati)
    beat_schedule={
        # Esempio: pulizia task vecchi ogni giorno
        # "cleanup-old-tasks": {
        #     "task": "tasks.maintenance.cleanup_old_tasks",
        #     "schedule": crontab(hour=2, minute=0),
        # },
    },
)

# Task base class con retry automatico
class BaseTaskWithRetry(celery_app.Task):
    """Task base con retry automatico su errori transitori."""
    
    autoretry_for = (Exception,)
    retry_kwargs = {"max_retries": settings.CELERY_MAX_RETRIES}
    retry_backoff = True  # Exponential backoff
    retry_backoff_max = 600  # Max 10 minuti tra retry
    retry_jitter = True  # Aggiunge randomness


# Registra task base
celery_app.Task = BaseTaskWithRetry


if __name__ == "__main__":
    print("=" * 50)
    print("🚀 CELERY CONFIGURATION")
    print("=" * 50)
    print(f"Broker:     {settings.CELERY_BROKER_URL}")
    print(f"Backend:    {settings.CELERY_RESULT_BACKEND}")
    print(f"Timeout:    {settings.CELERY_TASK_TIMEOUT}s")
    print(f"Expires:    {settings.CELERY_RESULT_EXPIRES}s")
    print("=" * 50)
    print("\nPer avviare il worker:")
    print("  celery -A celery_app worker --loglevel=info")
    print("\nPer monitorare (richiede flower):")
    print("  celery -A celery_app flower")
