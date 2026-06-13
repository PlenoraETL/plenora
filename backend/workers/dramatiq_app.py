"""Bootstrap Dramatiq (Plenora 2.0).

Registra gli actor con i nomi storici dei task (il dispatch by-name non
cambia i chiamanti) e la topologia di code da settings. Plenora 2.0 Fase 5:
i corpi dei task sono funzioni pure (niente piu' celery); l'esito finisce su
PostgreSQL via ``_TaskResultMiddleware``.

Worker: ``dramatiq workers.dramatiq_app`` (processi/thread via CLI).
Broker: ``DRAMATIQ_BROKER_URL`` (default RabbitMQ del compose).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware import CurrentMessage, Middleware

from app.config import settings
from app.core.orchestrator.task_progress import TaskRevoked
from app.utils.logger import get_logger, get_request_id, set_request_id

logger = get_logger(__name__)

# Chiave nelle ``message.options`` che trasporta il correlation id
# attraverso il broker (Dramatiq non ha header nativi: le options sono
# serializzate col messaggio). Rimpiazzo dell'header "X-Request-ID" che
# Celery propagava via signal before_task_publish.
_REQUEST_ID_OPTION = "request_id"

# Nessun path nell'URL = vhost di default "/" (un trailing "//" verrebbe
# interpretato da pika come vhost vuoto -> 530 NOT_ALLOWED).
_DEFAULT_BROKER_URL = "amqp://guest:guest@rabbitmq:5672"


def broker_url() -> str:
    return os.environ.get("DRAMATIQ_BROKER_URL", _DEFAULT_BROKER_URL)


class _TaskResultMiddleware(Middleware):
    """Persiste l'esito dell'actor su PostgreSQL (Plenora 2.0, Fase 2).

    Simmetrico a ``BaseTaskWithRetry.after_return`` lato Celery: dopo
    l'esecuzione di un actor scrive stato+risultato su task_result_store
    (keyed sul message_id), cosi' l'orchestratore — che dalla Fase 2 legge
    da PG — vede l'esito anche quando il task gira sul worker Dramatiq.
    Best-effort: finalize_from_celery non propaga."""

    def after_process_message(
        self,
        broker: object,
        message: object,
        *,
        result: object = None,
        exception: object = None,
    ) -> None:
        # finalize_from_celery mappa TaskRevoked -> REVOKED (F5.2),
        # SUCCESS/FAILURE altrimenti.
        from app.core.orchestrator.task_progress import finalize_from_celery

        status = "FAILURE" if exception is not None else "SUCCESS"
        einfo = (
            SimpleNamespace(exception=exception) if exception is not None else None
        )
        finalize_from_celery(
            message.message_id, status, result, einfo, name=message.actor_name
        )


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
    """Bootstrap del processo worker Dramatiq (Plenora 2.0, Fase 5).

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
# Finalize dell'esito su PG (rimpiazzo del result backend Redis): vale per
# tutti gli actor, come after_return per i task Celery.
broker.add_middleware(_TaskResultMiddleware())
dramatiq.set_broker(broker)


def current_message_id() -> str | None:
    """message_id del messaggio Dramatiq in esecuzione, o None fuori da un
    actor. Equivalente di `self.request.id` lato Celery."""
    message = CurrentMessage.get_current_message()
    return message.message_id if message is not None else None


def _invoke_task_body(task_fn: object, *, name: str, kwargs: dict) -> object:
    """Esegue il corpo di un task (funzione pura) con un self-shim Dramatiq.

    Plenora 2.0 Fase 5: i corpi dei task sono funzioni pure (niente piu'
    ``@celery_app.task``). Usano ``self`` solo per ``self.request.id``
    (task_id) e ``self.name`` (routing del progress su PG): lo shim li
    fornisce dal messaggio Dramatiq corrente. L'esito finale lo scrive
    ``_TaskResultMiddleware``.
    """
    shim = SimpleNamespace(
        request=SimpleNamespace(id=current_message_id()), name=name
    )
    return task_fn(shim, **kwargs)  # type: ignore[operator]


# Routing actor -> coda: stessa tabella di celery_app.task_routes (stessa
# fonte settings). Il contract test ne blinda la parita' finche' i due
# stack convivono.
TASK_QUEUE_ROUTES: dict[str, str] = {
    "pipeline.execute": settings.CELERY_QUEUE_DATA_PLANE,
    "pipeline.preview": settings.CELERY_QUEUE_DATA_PLANE,
    "carica.execute": settings.CELERY_QUEUE_DATA_PLANE,
    "tasks.execute_enrichment": settings.CELERY_QUEUE_DATA_PLANE,
    "workers.tasks.admin_backup_task.run_manual_backup_task": (
        settings.CELERY_QUEUE_CONTROL_PLANE
    ),
    "workers.tasks.cleanup_temp_files_task.run": (
        settings.CELERY_QUEUE_CONTROL_PLANE
    ),
    "workers.tasks.schedule_task.schedule_pipeline_task": (
        settings.CELERY_QUEUE_DATA_PLANE
    ),
    "workers.tasks.schedule_task.beat_heartbeat": (
        settings.CELERY_QUEUE_SCHEDULER
    ),
}


def queue_for(task_name: str) -> str:
    return TASK_QUEUE_ROUTES.get(task_name, settings.CELERY_QUEUE_DEFAULT)


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
    throws=(TaskRevoked,),
    max_retries=0,
)
def carica_execute(**kwargs: object) -> object:
    """CARICA — stesso corpo del task Celery carica.execute."""
    from workers.tasks.carica_task import execute_carica_task

    return _invoke_task_body(
        execute_carica_task, name="carica.execute", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="pipeline.execute",
    queue_name=queue_for("pipeline.execute"),
    throws=(TaskRevoked,),
    max_retries=0,
)
def pipeline_execute(**kwargs: object) -> object:
    """PIPELINE execute — stesso corpo del task Celery pipeline.execute."""
    from workers.tasks.pipeline_task import execute_pipeline_task

    return _invoke_task_body(
        execute_pipeline_task, name="pipeline.execute", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="pipeline.preview",
    queue_name=queue_for("pipeline.preview"),
    throws=(TaskRevoked,),
    max_retries=0,
)
def pipeline_preview(**kwargs: object) -> object:
    """PIPELINE preview — stesso corpo del task Celery pipeline.preview."""
    from workers.tasks.pipeline_task import preview_pipeline_task

    return _invoke_task_body(
        preview_pipeline_task, name="pipeline.preview", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="tasks.execute_enrichment",
    queue_name=queue_for("tasks.execute_enrichment"),
    throws=(TaskRevoked,),
    max_retries=0,
)
def execute_enrichment(**kwargs: object) -> object:
    """ARRICCHISCI — stesso corpo del task Celery tasks.execute_enrichment."""
    from workers.tasks.enrichment_task import execute_enrichment_task

    return _invoke_task_body(
        execute_enrichment_task, name="tasks.execute_enrichment", kwargs=kwargs
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
    "queue_for",
    "run_manual_backup_task",
    "schedule_pipeline_task",
]
