"""
Configurazione applicazione Celery — broker e backend su Redis.

Broker:  redis://localhost:6379/0
Backend: redis://localhost:6379/1
Beat:    PlenoraBeatScheduler              (legge schedule da config.db)

Avvio worker (TEST ONLY):
    celery -A workers.celery_app worker --loglevel=info --pool=solo

Avvio worker (PRODUZIONE):
    celery -A workers.celery_app worker --loglevel=info --concurrency=4

NB (C7): `--pool=solo` e' solo per test locali o debug (single-threaded,
no concurrency). In produzione il systemd unit `plenora-celery.service`
usa il pool default `prefork` con `--concurrency=N` (piu' child worker
processi paralleli). Ogni child ha il proprio pool psycopg2 — vedi
`app/db/pg_connection.py` per il sizing PG aggregato.

Avvio Beat:
    celery -A workers.celery_app beat -S workers.beat_scheduler.PlenoraBeatScheduler --loglevel=info
"""

from celery import Celery
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

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
        "workers.tasks.imdf_publish_task",
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
    autoretry_for = (
        ConnectionError,
        ConnectionResetError,
        TimeoutError,
        OSError,
    )
    retry_kwargs = {"max_retries": settings.CELERY_MAX_RETRIES}
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True


celery_app.Task = BaseTaskWithRetry


# ============================================================
# C8: request_id propagation via Celery signals
# ============================================================
# Il request_id impostato nel ContextVar dal RequestIdMiddleware HTTP
# deve seguire il task attraverso il broker Redis fino al worker child
# che lo esegue. Senza questi signal handler, il task parte con
# request_id vuoto nel worker (la ContextVar del parent non viene
# serializzata).
#
# - before_task_publish: sender-side, legge request_id dal ContextVar
#   corrente (del chiamante, es. request HTTP) e lo mette nei task
#   headers.
# - task_prerun: worker-side, legge il request_id dai task headers e lo
#   setta nel ContextVar del worker prima che il task body esegua.
# - task_postrun: worker-side, reset difensivo della ContextVar (evita
#   che un task legga request_id di uno precedente se il worker non
#   rigenera il contesto).

from celery.signals import before_task_publish, task_prerun, task_postrun


@before_task_publish.connect
def _propagate_request_id_on_publish(sender=None, headers=None, **kwargs):
    """Copia il request_id dal ContextVar locale nei headers del task."""
    try:
        from app.utils.logger import get_request_id
        req_id = get_request_id()
        if req_id and headers is not None:
            # Celery usa headers dict "raw" — convenzione: chiave custom.
            headers["X-Request-ID"] = req_id
    except Exception as e:
        # Non bloccare mai la pubblicazione del task per un errore nel
        # propagate — il task stesso funziona comunque, solo perde il
        # correlation ID. Breadcrumb best-effort: se anche il log fallisce
        # (hot path signal handler), non propagare.
        try:
            logger.warning(
                f"[SIGNAL] before_task_publish: propagazione request_id "
                f"fallita (task perde correlation id): {e}"
            )
        except Exception:
            pass


@task_prerun.connect
def _set_request_id_from_task(task_id=None, task=None, **kwargs):
    """Restaura il request_id nel worker dal task headers."""
    try:
        from app.utils.logger import set_request_id
        headers = getattr(task.request, "headers", None) or {}
        req_id = headers.get("X-Request-ID") or ""
        # Fallback: se non c'e' un request_id propagato (task schedulato
        # da beat, o invocazione legacy), usa il task_id come correlation
        # cosi' c'e' sempre un id per correlare le righe di log di un
        # singolo task.
        set_request_id(req_id or (task_id or ""))
    except Exception as e:
        # Breadcrumb best-effort: il worker continua (log del task perdera'
        # correlation id, ma il task non deve crashare per questo).
        try:
            logger.warning(
                f"[SIGNAL] task_prerun: set request_id fallito per "
                f"task_id={task_id}: {e}"
            )
        except Exception:
            pass


@task_postrun.connect
def _clear_request_id_after_task(**kwargs):
    """Reset ContextVar dopo il task per evitare leak tra task
    consecutivi nello stesso worker child."""
    try:
        from app.utils.logger import set_request_id
        set_request_id("")
    except Exception as e:
        # Breadcrumb best-effort: se il reset fallisce, il prossimo task
        # nello stesso worker potrebbe ereditare il request_id precedente
        # — diagnosi utile per correlation leak cross-task.
        try:
            logger.warning(
                f"[SIGNAL] task_postrun: reset request_id fallito: {e}"
            )
        except Exception:
            pass


if __name__ == "__main__":
    print("=" * 50)
    print("🚀 CELERY CONFIGURATION (Redis)")
    print("=" * 50)
    print(f"Broker:  {BROKER_URL}")
    print(f"Backend: {BACKEND_URL}")
    print(f"Timeout: {settings.CELERY_TASK_TIMEOUT}s")
    print("=" * 50)
    print("\nAvvio worker (TEST ONLY, single-thread):")
    print("  celery -A workers.celery_app worker --loglevel=info --pool=solo")
    print("\nAvvio worker (PRODUZIONE, prefork):")
    print("  celery -A workers.celery_app worker --loglevel=info --concurrency=4")
    print("\nAvvio Beat:")
    print("  celery -A workers.celery_app beat -S workers.beat_scheduler.PlenoraBeatScheduler --loglevel=info")
