"""Bootstrap Dramatiq.

Registra gli actor con nomi stabili per il dispatch by-name e la topologia di
code da settings. I corpi dei task sono funzioni pure; l'esito finisce su
PostgreSQL via ``_TaskResultMiddleware``.

Worker: ``dramatiq workers.dramatiq_app`` (processi/thread via CLI).
Broker: ``DRAMATIQ_BROKER_URL`` (default RabbitMQ del compose).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from types import SimpleNamespace
from uuid import uuid4

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware import CurrentMessage, Middleware, SkipMessage

from app.config import settings
from app.config_env_loader import load_backend_dotenv
from app.core.orchestrator.task_progress import TaskRevoked
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.task_runtime.runtime_leases import (
    claim_task,
    finish_task_claim,
    heartbeat_task_claims,
    heartbeat_worker,
    register_worker,
    unregister_worker,
)
from app.utils.logger import get_logger, get_request_id, set_request_id

# Bare-metal (Windows/Linux senza compose): DRAMATIQ_BROKER_URL vive nel
# .env, ma il broker viene costruito piu' sotto all'import del modulo —
# prima che app.bootstrap.bootstrap() (before_worker_boot) carichi il
# dotenv. Senza questo load anticipato il worker consumava dal default
# compose `rabbitmq` (DNS inesistente fuori da Docker) mentre
# ensure_broker, post-dotenv, validava l'URL giusto del .env. Il loader e'
# idempotente e ignora il .env in prod/staging.
load_backend_dotenv()

logger = get_logger(__name__)

# Chiave nelle ``message.options`` che trasporta il correlation id
# attraverso il broker (Dramatiq non ha header nativi: le options sono
# serializzate col messaggio). Rimpiazzo dell'header "X-Request-ID" che
# Celery propagava via signal before_task_publish.
_REQUEST_ID_OPTION = "request_id"

_RECYCLE_LOCK = threading.Lock()
_ACTIVE_MESSAGES = 0
_RECYCLE_REQUESTED = False

_RUN_BACKED_ACTORS = frozenset(
    {
        "pipeline.execute",
        "carica.execute",
        "tasks.execute_enrichment",
    }
)


class RetryablePipelineError(RuntimeError):
    """Sanitized signal used only for idempotent transient pipeline retries."""


def _return_or_raise_retryable(result: object) -> object:
    if isinstance(result, dict) and result.get("retryable") is True:
        raise RetryablePipelineError("Transient pipeline infrastructure failure")
    return result


def broker_url() -> str:
    # Settings legge l'env live; il dotenv e' gia' stato caricato sopra.
    return settings.DRAMATIQ_BROKER_URL


class _TaskResultMiddleware(Middleware):
    """Persiste l'esito dell'actor su PostgreSQL.

    Dopo l'esecuzione di un actor scrive stato+risultato su task_result_store
    (keyed sul message_id), cosi' l'orchestratore vede l'esito dal worker.
    Best-effort: finalize_from_celery non propaga."""

    def after_process_message(
        self,
        broker: object,
        message: object,
        *,
        result: object = None,
        exception: object = None,
    ) -> None:
        # finalize_from_celery mappa TaskRevoked -> REVOKED,
        # SUCCESS/FAILURE altrimenti.
        from app.core.orchestrator.task_progress import (
            finalize_from_celery,
            finalize_task_result,
        )

        message_kwargs = getattr(message, "kwargs", None)
        requested_run_id = (
            str(message_kwargs.get("run_id") or "")
            if isinstance(message_kwargs, dict)
            else ""
        )
        effective_run_id = (
            (requested_run_id or str(message.message_id))
            if message.actor_name in _RUN_BACKED_ACTORS
            else ""
        )

        if isinstance(exception, RetryablePipelineError):
            retries = int(getattr(message, "options", {}).get("retries", 0) or 0)
            actor = broker.get_actor(message.actor_name)
            max_retries = int(actor.options.get("max_retries", 0) or 0)
            if retries < max_retries:
                finalize_task_result(
                    message.message_id,
                    state="RETRY",
                    payload={
                        "success": False,
                        "retryable": True,
                        "message": "Errore transitorio: nuovo tentativo in corso",
                        "run_id": effective_run_id,
                    },
                    name=message.actor_name,
                )
                return

        status = "FAILURE" if exception is not None else "SUCCESS"
        einfo = (
            SimpleNamespace(exception=exception) if exception is not None else None
        )
        finalize_from_celery(
            message.message_id,
            status,
            result,
            einfo,
            name=message.actor_name,
            run_id=effective_run_id,
        )


def _worker_memory_recycle_cap_bytes() -> int:
    """RSS cap above which a worker process should recycle post-task."""
    raw_cap = settings.get(
        "WORKER_MEMORY_RECYCLE_RSS_BYTES",
        settings.get("WORKER_RSS_MAX_BYTES", 0),
    )
    try:
        return int(raw_cap or 0)
    except (TypeError, ValueError):
        logger.warning(
            "[MEMORY] invalid WORKER_MEMORY_RECYCLE_RSS_BYTES=%r; "
            "worker recycle disabled",
            raw_cap,
        )
        return 0


class _WorkerMemoryRecycleMiddleware(Middleware):
    """Recycle worker process if post-task cleanup leaves RSS above cap.

    Cleanup can release Python objects yet leave the long-lived process RSS
    above `WORKER_RSS_MAX_BYTES` because native libraries/allocators retain
    memory. In that state the next pipeline is rejected by the RSS guard
    before doing useful work, so the only reliable recovery is process recycle.
    """

    def before_process_message(self, broker: object, message: object) -> None:
        global _ACTIVE_MESSAGES
        with _RECYCLE_LOCK:
            _ACTIVE_MESSAGES += 1

    def after_ack(self, broker: object, message: object) -> None:
        self._complete_message_and_recycle_if_needed(message)

    def after_nack(self, broker: object, message: object) -> None:
        self._complete_message_and_recycle_if_needed(message)

    def _complete_message_and_recycle_if_needed(self, message: object) -> None:
        global _ACTIVE_MESSAGES
        with _RECYCLE_LOCK:
            if _ACTIVE_MESSAGES > 0:
                _ACTIVE_MESSAGES -= 1
        self._recycle_if_over_cap(message)

    def _recycle_if_over_cap(
        self,
        message: object,
    ) -> None:
        global _RECYCLE_REQUESTED
        if not settings.get("WORKER_MEMORY_RECYCLE_ENABLED", True):
            return

        cap_bytes = _worker_memory_recycle_cap_bytes()
        if cap_bytes <= 0:
            return

        try:
            from app.core.resources import memory_guard

            rss = memory_guard.get_current_rss_bytes()
        except Exception:
            logger.warning("[MEMORY] worker recycle RSS sample failed", exc_info=True)
            return

        over_cap = rss is not None and rss > cap_bytes
        if over_cap:
            with _RECYCLE_LOCK:
                _RECYCLE_REQUESTED = True
        else:
            with _RECYCLE_LOCK:
                recycle_requested = _RECYCLE_REQUESTED
            if not recycle_requested:
                return

        with _RECYCLE_LOCK:
            active_messages = _ACTIVE_MESSAGES
            recycle_requested = _RECYCLE_REQUESTED

        if not recycle_requested:
            return

        if active_messages > 0:
            logger.warning(
                "[MEMORY] worker recycle requested after %s; waiting for "
                "%s active message(s) before exit",
                getattr(message, "actor_name", "unknown"),
                active_messages,
            )
            return

        reason = "rss-over-cap" if over_cap else "pending-recycle"
        logger.warning(
            "[MEMORY] worker recycle %s after %s; rss=%s cap=%s; "
            "exiting process for supervisor restart",
            reason,
            getattr(message, "actor_name", "unknown"),
            rss,
            cap_bytes,
        )
        logging.shutdown()
        os._exit(0)


def _bootstrap_worker_process(*, force_reset: bool = False) -> None:
    """Esegue il bootstrap 4-step nel processo worker via il boundary
    canonico ``app.bootstrap`` (rimpiazzo del signal Celery
    ``worker_process_init`` / ``worker_init``).

    ``force_reset=True``: i processi worker Dramatiq nascono da fork del
    supervisor; il pool psycopg ereditato non e' sicuro post-fork. Si
    azzera il flag di completamento e si ri-esegue il bootstrap cosi'
    ogni worker ha il proprio pool. Fail-closed: una qualsiasi eccezione
    da ``bootstrap`` propaga -> il worker non accetta task contro un DB /
    storage / broker mal configurato.

    Import locale: tiene leggero l'import del modulo (il broker viene
    importato anche dai processi che solo *dispatchano*, che non devono
    pagare l'import del grafo bootstrap)."""
    from app import bootstrap as _bootstrap_mod

    if force_reset:
        _bootstrap_mod._BOOT_COMPLETE = False

    if _bootstrap_mod.is_bootstrapped():
        return

    _bootstrap_mod.bootstrap()


class _BootstrapMiddleware(Middleware):
    """Bootstrap del processo worker Dramatiq.

    ``before_worker_boot`` scatta una volta per processo worker, dopo il
    fork del supervisor ``dramatiq``: e' il rimpiazzo del signal Celery
    ``worker_process_init``. Scarta il pool psycopg ereditato PRIMA di
    ri-bootstrappare (``bootstrap`` riapre la connessione DB), poi esegue
    i 4 step con ``force_reset=True``."""

    def before_worker_boot(self, broker: object, worker: object) -> None:
        # Drop del pool psycopg ereditato dal supervisor SENZA closeall():
        # il padre possiede ancora i socket sottostanti.
        from app.db.pg_connection import discard_pool_after_fork

        discard_pool_after_fork()
        _bootstrap_worker_process(force_reset=True)


class _WorkerLeaseMiddleware(Middleware):
    """Publish worker/task leases through PostgreSQL on every platform."""

    def __init__(self) -> None:
        self._worker_id: str | None = None
        self._role: str | None = None
        self._queues: tuple[str, ...] = ()
        self._active_task_ids: set[str] = set()
        self._active_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _worker_role(queues: tuple[str, ...]) -> str:
        if settings.TASK_QUEUE_DATA_PLANE in queues:
            return "data-plane"
        return "control-plane"

    def after_worker_boot(self, broker: object, worker: object) -> None:
        whitelist = getattr(worker, "consumer_whitelist", None) or ()
        self._queues = tuple(sorted(str(queue) for queue in whitelist))
        self._role = self._worker_role(self._queues)
        self._worker_id = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
        )
        register_worker(
            worker_id=self._worker_id,
            role=self._role,  # type: ignore[arg-type]
            queues=self._queues,
            hostname=socket.gethostname(),
            pid=os.getpid(),
        )
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"worker-lease-{self._role}",
            daemon=True,
        )
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        try:
            interval = max(
                1,
                int(settings.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", 10)),
            )
            lease_seconds = max(
                interval * 3,
                int(settings.get("TASK_EXECUTION_LEASE_SECONDS", 45)),
            )
        except (TypeError, ValueError):
            interval = 10
            lease_seconds = 45

        while not self._stop.wait(interval):
            worker_id = self._worker_id
            if not worker_id:
                return
            try:
                heartbeat_worker(worker_id)
                with self._active_lock:
                    active = tuple(self._active_task_ids)
                heartbeat_task_claims(
                    worker_id=worker_id,
                    task_ids=active,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                logger.exception(
                    "[WORKER-LEASE] heartbeat fallito worker_id=%s",
                    worker_id,
                )

    def before_process_message(self, broker: object, message: object) -> None:
        worker_id = self._worker_id
        if not worker_id:
            raise RuntimeError("Worker lease non inizializzato")
        task_id = str(getattr(message, "message_id", "") or "")
        actor_name = str(getattr(message, "actor_name", "") or "")
        if not task_id or not actor_name:
            raise RuntimeError("Messaggio Dramatiq senza task_id/actor_name")
        try:
            lease_seconds = max(
                1,
                int(settings.get("TASK_EXECUTION_LEASE_SECONDS", 45)),
            )
        except (TypeError, ValueError):
            lease_seconds = 45
        outcome = claim_task(
            task_id=task_id,
            actor_name=actor_name,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if outcome != "acquired":
            logger.warning(
                "[TASK-LEASE] duplicate task skipped task_id=%s actor=%s "
                "outcome=%s",
                task_id,
                actor_name,
                outcome,
            )
            raise SkipMessage()
        with self._active_lock:
            self._active_task_ids.add(task_id)

    def after_process_message(
        self,
        broker: object,
        message: object,
        *,
        result: object = None,
        exception: object = None,
    ) -> None:
        task_id = str(getattr(message, "message_id", "") or "")
        worker_id = self._worker_id
        if not task_id or not worker_id:
            return
        with self._active_lock:
            self._active_task_ids.discard(task_id)
        try:
            finish_task_claim(
                task_id=task_id,
                worker_id=worker_id,
                succeeded=exception is None,
            )
        except Exception:
            logger.exception(
                "[TASK-LEASE] finalize claim fallito task_id=%s",
                task_id,
            )

    def before_worker_shutdown(self, broker: object, worker: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        worker_id = self._worker_id
        if not worker_id:
            return
        try:
            unregister_worker(worker_id)
        except Exception:
            logger.warning(
                "[WORKER-LEASE] unregister fallito worker_id=%s",
                worker_id,
                exc_info=True,
            )


class _RequestIdMiddleware(Middleware):
    """Propaga il correlation id (X-Request-ID) attraverso il broker.

    Rimpiazzo dei signal Celery before_task_publish / task_prerun /
    task_postrun:
      - ``before_enqueue``: copia il request_id dal ContextVar locale nelle
        ``message.options`` (lato processo che dispatcha, es. API/beat);
      - ``before_process_message``: ripristina il request_id nel worker dal
        messaggio, con fallback al message_id come faceva il task_prerun;
      - ``after_process_message``: azzera il ContextVar per evitare leak di
        correlation tra task consecutivi sullo stesso thread worker.
    Best-effort: la correlation non deve mai bloccare enqueue/esecuzione."""

    def before_enqueue(self, broker: object, message: object, delay: object) -> None:
        try:
            req_id = get_request_id()
            if req_id:
                message.options[_REQUEST_ID_OPTION] = req_id
        except Exception:
            logger.warning(
                "[REQID] propagazione request_id su enqueue fallita "
                "(task perde correlation id)",
                exc_info=True,
            )

    def before_process_message(self, broker: object, message: object) -> None:
        try:
            options = getattr(message, "options", None) or {}
            req_id = options.get(_REQUEST_ID_OPTION) or getattr(
                message, "message_id", ""
            )
            set_request_id(req_id or "")
        except Exception:
            logger.warning(
                "[REQID] set request_id nel worker fallito", exc_info=True
            )

    def after_process_message(
        self,
        broker: object,
        message: object,
        *,
        result: object = None,
        exception: object = None,
    ) -> None:
        try:
            set_request_id("")
        except Exception:
            logger.warning(
                "[REQID] reset request_id dopo il task fallito", exc_info=True
            )


broker = RabbitmqBroker(url=broker_url())
# Bootstrap del processo worker (4-step su boundary app.bootstrap): scatta
# a worker_boot, rimpiazzo del signal Celery worker_process_init.
broker.add_middleware(_BootstrapMiddleware())
# Cross-platform worker presence, task leases and message-id deduplication.
broker.add_middleware(_WorkerLeaseMiddleware())
# CurrentMessage espone il messaggio in esecuzione all'actor: e' il
# rimpiazzo Dramatiq di `self.request.id` (bind=True) di Celery, usato
# dai task che mettono il task_id nel result payload.
broker.add_middleware(CurrentMessage())
# Propagazione del correlation id attraverso il broker (rimpiazzo dei
# signal Celery before_task_publish/task_prerun/task_postrun). Registrato
# PRIMA del finalize cosi' l'after_process_message del finalize (che gira
# in ordine inverso) logga ancora con il request_id, e il reset avviene
# per ultimo.
broker.add_middleware(_RequestIdMiddleware())
# Riciclo memoria post-task: usa after_ack/after_nack, quindi il messaggio e'
# gia' stato confermato/rifiutato al broker prima dell'eventuale os._exit.
broker.add_middleware(_WorkerMemoryRecycleMiddleware())
# Finalize dell'esito su PG (rimpiazzo del result backend Redis): vale per
# tutti gli actor, come after_return per i task Celery.
broker.add_middleware(_TaskResultMiddleware())
dramatiq.set_broker(broker)


def current_message_id() -> str | None:
    """message_id del messaggio Dramatiq in esecuzione, o None fuori da un
    actor. Equivalente di `self.request.id` lato Celery."""
    message = CurrentMessage.get_current_message()
    return message.message_id if message is not None else None


def _cleanup_worker_memory_after_task(name: str) -> None:
    """Best-effort RSS cleanup after data-plane task bodies.

    Pandas/pyarrow task bodies can release Python objects while the process
    RSS remains high because allocator arenas are retained. This cleanup keeps
    long-lived Dramatiq worker processes reusable between heavy tasks.
    """
    if not settings.get("WORKER_MEMORY_CLEANUP_ENABLED", True):
        return

    try:
        from app.core.resources import memory_guard

        stats = memory_guard.cleanup_worker_memory(
            run_gc=True,
            run_malloc_trim=bool(
                settings.get("WORKER_MEMORY_MALLOC_TRIM_ENABLED", True)
            ),
        )
        before = stats.get("before_rss")
        after = stats.get("after_rss")
        if isinstance(before, int) and isinstance(after, int):
            freed = before - after
            if freed > 0:
                logger.info(
                    "[MEMORY] worker cleanup after %s: rss %s -> %s bytes "
                    "(freed=%s, gc=%s, malloc_trim=%s)",
                    name,
                    before,
                    after,
                    freed,
                    stats.get("collected"),
                    stats.get("malloc_trim"),
                )
    except Exception:
        logger.warning(
            "[MEMORY] worker cleanup after %s failed", name, exc_info=True
        )


def _invoke_task_body(task_fn: object, *, name: str, kwargs: dict) -> object:
    """Esegue il corpo di un task (funzione pura) con un self-shim Dramatiq.

    I corpi dei task sono funzioni pure. Usano ``self`` solo per
    ``self.request.id`` (task_id) e ``self.name`` (routing del progress su PG):
    lo shim li
    fornisce dal messaggio Dramatiq corrente. L'esito finale lo scrive
    ``_TaskResultMiddleware``.
    """
    shim = SimpleNamespace(
        request=SimpleNamespace(id=current_message_id()), name=name
    )
    try:
        return task_fn(shim, **kwargs)  # type: ignore[operator]
    finally:
        _cleanup_worker_memory_after_task(name)


def _data_plane_execution_mode() -> str:
    return str(
        settings.get("DATA_PLANE_EXECUTION_MODE", "subprocess") or "subprocess"
    ).strip().lower()


def _data_plane_child_timeout_seconds() -> int:
    return int(
        settings.get(
            "DATA_PLANE_CHILD_TIMEOUT_SECONDS",
            settings.get("TASK_HARD_LIMIT", 3900),
        )
    )


def _data_plane_child_memory_limit_bytes() -> int:
    return int(
        settings.get(
            "DATA_PLANE_CHILD_RSS_MAX_BYTES",
            settings.get("WORKER_RSS_MAX_BYTES", 1_500_000_000),
        )
    )


# Routing actor -> coda: stessa tabella di celery_app.task_routes (stessa
# fonte settings). Il contract test ne blinda la parita' finche' i due
# stack convivono.
TASK_QUEUE_ROUTES: dict[str, str] = {
    "pipeline.execute": settings.TASK_QUEUE_DATA_PLANE,
    "pipeline.preview": settings.TASK_QUEUE_DATA_PLANE,
    "carica.execute": settings.TASK_QUEUE_DATA_PLANE,
    "tasks.execute_enrichment": settings.TASK_QUEUE_DATA_PLANE,
    "workers.tasks.admin_backup_task.run_manual_backup_task": (
        settings.TASK_QUEUE_CONTROL_PLANE
    ),
    "workers.tasks.cleanup_temp_files_task.run": (
        settings.TASK_QUEUE_CONTROL_PLANE
    ),
    "workers.tasks.schedule_task.schedule_pipeline_task": (
        settings.TASK_QUEUE_DATA_PLANE
    ),
    "workers.tasks.schedule_task.beat_heartbeat": (
        settings.TASK_QUEUE_SCHEDULER
    ),
}


def queue_for(task_name: str) -> str:
    return TASK_QUEUE_ROUTES.get(task_name, settings.TASK_QUEUE_DEFAULT)


# ---------------------------------------------------------------------------
# Actor pilota (fetta 2): cleanroom.ping — no-op identico al task Celery.
# I task applicativi veri migrano nelle fette successive, uno alla volta.
# ---------------------------------------------------------------------------


@dramatiq.actor(actor_name="cleanroom.ping", queue_name=queue_for("cleanroom.ping"))
def cleanroom_ping() -> str:
    """Return the canonical "pong" string. Pure no-op."""
    return "pong"


@dramatiq.actor(
    actor_name="cleanroom.beat_ping",
    queue_name=queue_for("cleanroom.beat_ping"),
)
def cleanroom_beat_ping() -> str:
    """Return the canonical "beat_pong" string. Pure no-op."""
    return "beat_pong"


@dramatiq.actor(
    actor_name="workers.tasks.cleanup_temp_files_task.run",
    queue_name=queue_for("workers.tasks.cleanup_temp_files_task.run"),
    max_retries=0,
)
def cleanup_temp_files() -> dict:
    """Sweep temp file scaduti — corpo condiviso run_cleanup_temp_files.

    Import lazy: tiene leggero il bootstrap Dramatiq (il corpo importa
    moduli applicativi solo quando l'actor viene eseguito).
    """
    from workers.tasks.cleanup_temp_files_task import run_cleanup_temp_files

    return dict(run_cleanup_temp_files())


@dramatiq.actor(
    actor_name="workers.tasks.admin_backup_task.run_manual_backup_task",
    queue_name=queue_for(
        "workers.tasks.admin_backup_task.run_manual_backup_task"
    ),
    max_retries=0,
)
def run_manual_backup_task(*, owner_token: str) -> dict:
    """Backup manuale — corpo condiviso run_manual_backup.

    Il task_id e' il message_id Dramatiq corrente. Import lazy come gli
    altri actor (bootstrap leggero).
    """
    from workers.tasks.admin_backup_task import run_manual_backup

    return dict(
        run_manual_backup(owner_token=owner_token, task_id=current_message_id())
    )


# ---------------------------------------------------------------------------
# Task applicativi. Stessi nomi/code storici; il corpo e' una funzione pura
# invocata con un self-shim (vedi _invoke_task_body). Import lazy per tenere
# leggero il bootstrap Dramatiq.
# ---------------------------------------------------------------------------


@dramatiq.actor(
    actor_name="carica.execute",
    queue_name=queue_for("carica.execute"),
    throws=(TaskRevoked, MemoryLimitExceededError),
    max_retries=0,
)
def carica_execute(**kwargs: object) -> object:
    """CARICA isolato in un processo data-plane usa-e-getta."""
    task_kwargs = dict(kwargs)
    message_id = current_message_id()
    if message_id:
        task_kwargs.setdefault("run_id", message_id)
    execution_mode = _data_plane_execution_mode()
    if execution_mode == "subprocess":
        from workers.data_plane_subprocess import execute_task_in_subprocess

        return execute_task_in_subprocess(
            task_name="carica.execute",
            task_id=current_message_id() or "",
            kwargs=task_kwargs,
            timeout_seconds=_data_plane_child_timeout_seconds(),
            memory_limit_bytes=_data_plane_child_memory_limit_bytes(),
            request_id=get_request_id(),
        )
    if execution_mode != "inline":
        raise RuntimeError(
            "DATA_PLANE_EXECUTION_MODE deve essere inline o subprocess, "
            f"ricevuto {execution_mode!r}"
        )
    from workers.tasks.carica_task import execute_carica_task

    return _invoke_task_body(
        execute_carica_task, name="carica.execute", kwargs=task_kwargs
    )


@dramatiq.actor(
    actor_name="pipeline.execute",
    queue_name=queue_for("pipeline.execute"),
    throws=(TaskRevoked, MemoryLimitExceededError),
    max_retries=2,
    min_backoff=1_000,
    max_backoff=10_000,
)
def pipeline_execute(**kwargs: object) -> object:
    """PIPELINE execute — stesso corpo del task Celery pipeline.execute."""
    task_kwargs = dict(kwargs)
    # Stable across Dramatiq attempts: open_run is an upsert and pipeline
    # completion idempotency can recognize a successful previous attempt.
    message_id = current_message_id()
    if message_id:
        task_kwargs.setdefault("run_id", message_id)
    execution_mode = _data_plane_execution_mode()
    if execution_mode == "subprocess":
        from workers.data_plane_subprocess import execute_task_in_subprocess

        result = execute_task_in_subprocess(
            task_name="pipeline.execute",
            task_id=current_message_id() or "",
            kwargs=task_kwargs,
            timeout_seconds=_data_plane_child_timeout_seconds(),
            memory_limit_bytes=_data_plane_child_memory_limit_bytes(),
            request_id=get_request_id(),
        )
        return _return_or_raise_retryable(result)
    if execution_mode != "inline":
        raise RuntimeError(
            "DATA_PLANE_EXECUTION_MODE deve essere inline o subprocess, "
            f"ricevuto {execution_mode!r}"
        )

    from workers.tasks.pipeline_task import execute_pipeline_task

    result = _invoke_task_body(
        execute_pipeline_task, name="pipeline.execute", kwargs=task_kwargs
    )
    return _return_or_raise_retryable(result)


@dramatiq.actor(
    actor_name="pipeline.preview",
    queue_name=queue_for("pipeline.preview"),
    throws=(TaskRevoked, MemoryLimitExceededError),
    max_retries=0,
)
def pipeline_preview(**kwargs: object) -> object:
    """PIPELINE preview — isolata come pipeline.execute nel data-plane."""
    execution_mode = _data_plane_execution_mode()
    if execution_mode == "subprocess":
        from workers.data_plane_subprocess import execute_task_in_subprocess

        return execute_task_in_subprocess(
            task_name="pipeline.preview",
            task_id=current_message_id() or "",
            kwargs=dict(kwargs),
            timeout_seconds=_data_plane_child_timeout_seconds(),
            memory_limit_bytes=_data_plane_child_memory_limit_bytes(),
            request_id=get_request_id(),
        )
    if execution_mode != "inline":
        raise RuntimeError(
            "DATA_PLANE_EXECUTION_MODE deve essere inline o subprocess, "
            f"ricevuto {execution_mode!r}"
        )

    from workers.tasks.pipeline_task import preview_pipeline_task

    return _invoke_task_body(
        preview_pipeline_task, name="pipeline.preview", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="tasks.execute_enrichment",
    queue_name=queue_for("tasks.execute_enrichment"),
    throws=(TaskRevoked, MemoryLimitExceededError),
    max_retries=0,
)
def execute_enrichment(**kwargs: object) -> object:
    """ARRICCHISCI isolato in un processo data-plane usa-e-getta."""
    task_kwargs = dict(kwargs)
    message_id = current_message_id()
    if message_id:
        task_kwargs.setdefault("run_id", message_id)
    execution_mode = _data_plane_execution_mode()
    if execution_mode == "subprocess":
        from workers.data_plane_subprocess import execute_task_in_subprocess

        return execute_task_in_subprocess(
            task_name="tasks.execute_enrichment",
            task_id=current_message_id() or "",
            kwargs=task_kwargs,
            timeout_seconds=_data_plane_child_timeout_seconds(),
            memory_limit_bytes=_data_plane_child_memory_limit_bytes(),
            request_id=get_request_id(),
        )
    if execution_mode != "inline":
        raise RuntimeError(
            "DATA_PLANE_EXECUTION_MODE deve essere inline o subprocess, "
            f"ricevuto {execution_mode!r}"
        )

    from workers.tasks.enrichment_task import execute_enrichment_task

    return _invoke_task_body(
        execute_enrichment_task,
        name="tasks.execute_enrichment",
        kwargs=task_kwargs,
    )


@dramatiq.actor(
    actor_name="workers.tasks.schedule_task.schedule_pipeline_task",
    queue_name=queue_for("workers.tasks.schedule_task.schedule_pipeline_task"),
    max_retries=0,
)
def schedule_pipeline_task(**kwargs: object) -> object:
    """Schedule pipeline — stesso corpo del task Celery omonimo."""
    from workers.tasks.schedule_task import schedule_pipeline_task as _task

    return _invoke_task_body(
        _task,
        name="workers.tasks.schedule_task.schedule_pipeline_task",
        kwargs=kwargs,
    )


@dramatiq.actor(
    actor_name="workers.tasks.schedule_task.beat_heartbeat",
    queue_name=queue_for("workers.tasks.schedule_task.beat_heartbeat"),
    max_retries=0,
)
def beat_heartbeat() -> object:
    """Beat heartbeat — stesso corpo del task (funzione pura, niente self)."""
    from workers.tasks.schedule_task import beat_heartbeat as _task

    return _task()


__all__ = [
    "TASK_QUEUE_ROUTES",
    "beat_heartbeat",
    "broker",
    "broker_url",
    "carica_execute",
    "cleanroom_beat_ping",
    "cleanroom_ping",
    "cleanup_temp_files",
    "current_message_id",
    "execute_enrichment",
    "pipeline_execute",
    "pipeline_preview",
    "RetryablePipelineError",
    "queue_for",
    "run_manual_backup_task",
    "schedule_pipeline_task",
]
