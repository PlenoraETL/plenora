"""
Configurazione applicazione Celery — broker e backend su Redis.

Broker: redis://localhost:6379/0
"""

from celery import Celery
from app.config import settings
from app.environment import env_flag
from app.utils.logger import get_logger, get_request_id, set_request_id

logger = get_logger(__name__)

BROKER_URL = settings.CELERY_BROKER_URL
BACKEND_URL = settings.CELERY_RESULT_BACKEND

celery_app = Celery(
    "dataflow_tasks",
    broker=BROKER_URL,
    backend=BACKEND_URL,
    include=[
        "workers.tasks.enrichment_task",
        "workers.tasks.carica_task",
        "workers.tasks.schedule_task",
        "workers.tasks.admin_backup_task",
        "workers.tasks.pipeline_task",
        "workers.tasks.imdf_publish_task",
        "workers.tasks.cleanroom_task",
        "workers.tasks.cleanroom_beat_task",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Rome",
    enable_utc=True,
    result_expires=settings.CELERY_RESULT_EXPIRES,
    result_extended=True,
    task_track_started=True,
    task_send_sent_event=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.CELERY_TASK_TIMEOUT - 60,
    task_time_limit=settings.CELERY_TASK_TIMEOUT,
    task_default_rate_limit="100/m",
    broker_connection_retry_on_startup=True,
)


if env_flag("PLENORA_CLEANROOM_BEAT_PING"):
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

    # Retry only infrastructure/network errors; application errors must surface.
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


from celery.signals import (
    before_task_publish,
    task_prerun,
    task_postrun,
    worker_init,
    worker_process_init,
    beat_init,
)


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
        # Prefork children inherit a completed flag but not a safe PG pool.
        _bootstrap_mod._BOOT_COMPLETE = False

    if _bootstrap_mod.is_bootstrapped():
        return

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
    child has its own pool / Redis client / settings cache.

    Order matters: ``discard_pool_after_fork()`` must run BEFORE
    ``bootstrap()`` re-enters DB code. Without it, the bootstrap step
    that opens a connection (e.g. ``init_db`` / migrations runner)
    grabs the inherited TLS socket and dies with
    ``SSL SYSCALL error: EOF detected``. The first task assigned to
    that child then fails with ``connection already closed`` because
    ``_bootstrap_on_worker_process_init`` raised, leaving the child in
    a half-initialized state."""
    # Drop inherited psycopg2 pool reference WITHOUT closeall() — the
    # parent still owns the underlying TLS sockets.
    from app.db.pg_connection import discard_pool_after_fork
    discard_pool_after_fork()
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
        req_id = get_request_id()
        if req_id and headers is not None:
            headers["X-Request-ID"] = req_id
    except Exception as e:
        # Correlation propagation is best-effort; never block task publish.
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
        headers = getattr(task.request, "headers", None) or {}
        req_id = headers.get("X-Request-ID") or ""
        set_request_id(req_id or (task_id or ""))
    except Exception as e:
        # Correlation setup must not make the task fail.
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
        set_request_id("")
    except Exception as e:
        # Log potential cross-task correlation leaks without masking task result.
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
