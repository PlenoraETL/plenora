"""
Configurazione applicazione Celery — broker e backend su Redis.

Broker:  redis://localhost:6379/0
Backend: redis://localhost:6379/1
Beat:    UrbyBeatScheduler              (legge schedule da config.db)

Avvio worker:
    celery -A workers.celery_app worker --loglevel=info --pool=solo

Avvio Beat:
    celery -A workers.celery_app beat -S workers.beat_scheduler.UrbyBeatScheduler --loglevel=info
"""

from celery import Celery
from app.config import settings

# Redis come broker e backend
BROKER_URL  = settings.CELERY_BROKER_URL    # redis://localhost:6379/0
BACKEND_URL = settings.CELERY_RESULT_BACKEND  # redis://localhost:6379/1

celery_app = Celery(
    "dataflow_tasks",
    broker=BROKER_URL,
    backend=BACKEND_URL,
    include=[
        "workers.tasks.enrichment_task",
        "workers.tasks.carica_task",
        "workers.tasks.schedule_task",
        "workers.tasks.pipeline_task",
    ]
)

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
    result_extended=True,

    # Task tracking
    task_track_started=True,
    task_send_sent_event=True,

    # Worker
    worker_prefetch_multiplier=1,

    # Retry policy
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Timeout
    task_soft_time_limit=settings.CELERY_TASK_TIMEOUT - 60,
    task_time_limit=settings.CELERY_TASK_TIMEOUT,

    # Rate limiting
    task_default_rate_limit="100/m",

    # Redis broker — connection pool e health check
    broker_connection_retry_on_startup=True,
)


class BaseTaskWithRetry(celery_app.Task):
    """Task base con retry automatico su errori transitori."""
    # Retry SOLO su errori di rete/infrastruttura transitori, non su errori applicativi
    # (SQL errato, connessione chiusa dall'utente, credenziali errate, ecc.)
    autoretry_for = ()  # Nessun retry automatico — gestiamo esplicitamente in pipeline_task
    retry_kwargs = {"max_retries": settings.CELERY_MAX_RETRIES}
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True


celery_app.Task = BaseTaskWithRetry


if __name__ == "__main__":
    print("=" * 50)
    print("🚀 CELERY CONFIGURATION (Redis)")
    print("=" * 50)
    print(f"Broker:  {BROKER_URL}")
    print(f"Backend: {BACKEND_URL}")
    print(f"Timeout: {settings.CELERY_TASK_TIMEOUT}s")
    print("=" * 50)
    print("\nAvvio worker:")
    print("  celery -A workers.celery_app worker --loglevel=info --pool=solo")
    print("\nAvvio Beat:")
    print("  celery -A workers.celery_app beat -S workers.beat_scheduler.UrbyBeatScheduler --loglevel=info")
