"""
Configurazione applicazione Celery — broker e backend su Redis.

Broker: redis://localhost:6379/0
"""

import os

from celery import Celery
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Redis come broker e backend
BROKER_URL  = settings.CELERY_BROKER_URL    # redis://localhost:6379/0
BACKEND_URL = settings.CELERY_RESULT_BACKEND  # redis://localhost:6379/0

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
        "workers.tasks.cleanroom_task",
        "workers.tasks.cleanroom_beat_task",
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


if os.getenv("PLENORA_CLEANROOM_BEAT_PING") == "1":
    from celery.schedules import schedule as _celery_schedule

    celery_app.conf.beat_schedule = {
        **(celery_app.conf.beat_schedule or {}),
        "cleanroom-beat-ping": {
            "task": "cleanroom.beat_ping",
            "schedule": _celery_schedule(run_every=5.0),
        },
    }
    logger.info(
        "[CLEANROOM] PLENORA_CLEANROOM_BEAT_PING=1: registered "
        "cleanroom.beat_ping at 5s interval in beat_schedule"
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


# C8: request_id propagation via Celery signals
# Il request_id impostato nel ContextVar dal RequestIdMiddleware HTTP
# deve seguire il task attraverso il broker Redis fino al worker child
# che lo esegue. Senza questi signal handler, il task parte con
# request_id vuoto nel worker (la ContextVar del parent non viene
# serializzata
# before_task_publish: sender-side, legge request_id dal ContextVar
# corrente (del chiamante, es. request HTTP) e lo mette nei task
# headers
# task_prerun: worker-side, legge il request_id dai task headers e lo
# setta nel ContextVar del worker prima che il task body esegua
# task_postrun: worker-side, reset difensivo della ContextVar (evita
# che un task legga request_id di uno precedente se il worker non
# rigenera il contesto

from celery.signals import (
    before_task_publish,
    task_prerun,
    task_postrun,
    worker_init,
    worker_process_init,
    beat_init,
)


# worker bootstrap parity
# Web boot path (`app.main`) calls `app.bootstrap.bootstrap` once
# before any router import. Workers and beat must cross the SAME
# four-step boundary (validate_startup → validate_storage_config →
# init_all_dbs → _repair_psycopg2_ssl_env) so they fail-fast on
# config/storage/DB problems instead of discovering them at first
# task execution
# Coverage matrix
# `worker_init`: fires once in the worker MAIN process. Covers
# solo / threads / gevent pools (no fork). On prefork it ALSO
# fires in the parent — that bootstrap is harmless but the
# resulting psycopg2 pool is fork-unsafe; child workers re-init
# in `worker_process_init` (force_reset=True) post-fork
# `worker_process_init`: fires in EACH prefork child after
# fork. Forces re-bootstrap so the inherited (broken) pool is
# replaced with a child-local one
# `beat_init`: fires once on the beat scheduler process. Same
# boundary as the worker entry
# Idempotency: `is_bootstrapped` short-circuits a second call in
# the same process when no force_reset is requested. Combined with
# the underlying steps' own idempotency (validate_startup
# init_all_dbs are re-runnable), the handler is safe to fire
# multiple times
# Fail-closed: any exception from `bootstrap` propagates out of
# the signal handler. Celery treats raised signal handler exceptions
# as fatal → the worker / beat process exits → systemd / k8s
# restart loop surfaces the misconfiguration. NEVER swallow the
# exception here: bootstrap failure means config/storage/DB is
# wrong and the worker MUST NOT start accepting tasks


def _bootstrap_worker_process(*, force_reset: bool = False) -> None:
    """Bootstrap the worker process via the canonical boundary.

    Args:
        force_reset: when True, drop the in-process completion flag
            before bootstrap so the four steps re-run. Required in
            prefork children whose inherited psycopg2 pool is not
            safe to use post-fork. Default False = idempotent
            short-circuit on `is_bootstrapped`.

    Fail-closed: re-raises any exception from `bootstrap`. Celery
    propagates it out of the signal handler → process exit →
    supervisor restart. The `try/except` around the optional
    breadcrumb log is the ONLY swallowed boundary, and only for
    the log itself; the bootstrap exception always propagates.
    """
    from app import bootstrap as _bootstrap_mod

    if force_reset:
        # Post-fork in prefork children: the parent's `_BOOT_COMPLETE`
        # value is inherited but the underlying PG pool is broken
        # Reset the flag so the idempotent short-circuit does NOT skip
        # the re-init
        _bootstrap_mod._BOOT_COMPLETE = False

    if _bootstrap_mod.is_bootstrapped():
        # Already bootstrapped in this process and force_reset=False
        # nothing to do (e.g. worker_init firing on solo pool after
        # an earlier programmatic bootstrap call
        return

    # bootstrap raises on failure; let it propagate
    _bootstrap_mod.bootstrap()


@worker_init.connect
def _bootstrap_on_worker_init(**kwargs) -> None:
    """Worker main process startup. Covers solo / threads / gevent
    pools where no `worker_process_init` fires. On prefork this also
    fires in the master; the resulting bootstrap is wasteful but
    not harmful (child workers re-init via `worker_process_init`).
    """
    _bootstrap_worker_process()


@worker_process_init.connect
def _bootstrap_on_worker_process_init(**kwargs) -> None:
    """Prefork child process post-fork. The psycopg2 pool inherited
    from the master is NOT safe to use; force re-bootstrap so each
    child has its own pool / Redis client / settings cache."""
    _bootstrap_worker_process(force_reset=True)


@beat_init.connect
def _bootstrap_on_beat_init(**kwargs) -> None:
    """Beat scheduler process startup. Same four-step boundary as
    the web/worker entry."""
    _bootstrap_worker_process()


@before_task_publish.connect
def _propagate_request_id_on_publish(_sender=None, headers=None, **kwargs):
    """Copia il request_id dal ContextVar locale nei headers del task."""
    try:
        from app.utils.logger import get_request_id
        req_id = get_request_id()
        if req_id and headers is not None:
            # Celery usa headers dict "raw" — convenzione: chiave custom
            headers["X-Request-ID"] = req_id
    except Exception as e:
        # Non bloccare mai la pubblicazione del task per un errore nel
        # propagate — il task stesso funziona comunque, solo perde il
        # correlation ID. Breadcrumb best-effort: se anche il log fallisce
        # hot path signal handler), non propagare
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
        # singolo task
        set_request_id(req_id or (task_id or ""))
    except Exception as e:
        # Breadcrumb best-effort: il worker continua (log del task perdera'
        # correlation id, ma il task non deve crashare per questo
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
        # — diagnosi utile per correlation leak cross-task
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
