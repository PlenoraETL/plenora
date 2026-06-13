"""Bootstrap Dramatiq (Plenora 2.0, Fase 1) — convive con celery_app.

Strangler della migrazione broker: gli actor Dramatiq vengono registrati
QUI con gli STESSI nomi dei task Celery (il dispatch by-name di
DramatiqTaskRuntime non cambia i chiamanti) e con la STESSA topologia di
code (default/data_plane/control_plane/scheduler, da settings come in
celery_app.task_routes). Il default runtime resta Celery finche' il
result backend non migra (Fase 2): questo modulo permette di portare i
task uno alla volta e di verificarli end-to-end senza cutover.

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


broker = RabbitmqBroker(url=broker_url())
# CurrentMessage espone il messaggio in esecuzione all'actor: e' il
# rimpiazzo Dramatiq di `self.request.id` (bind=True) di Celery, usato
# dai task che mettono il task_id nel result payload.
broker.add_middleware(CurrentMessage())
# Finalize dell'esito su PG (rimpiazzo del result backend Redis): vale per
# tutti gli actor, come after_return per i task Celery.
broker.add_middleware(_TaskResultMiddleware())
dramatiq.set_broker(broker)


def current_message_id() -> str | None:
    """message_id del messaggio Dramatiq in esecuzione, o None fuori da un
    actor. Equivalente di `self.request.id` lato Celery."""
    message = CurrentMessage.get_current_message()
    return message.message_id if message is not None else None


def _invoke_celery_task_body(task: object, *, name: str, kwargs: dict) -> object:
    """Esegue il corpo di un task Celery (bind=True) con un self-shim Dramatiq.

    Dopo F2.5 i corpi dei task usano ``self`` solo per ``self.request.id``
    (task_id) e ``self.name`` (routing del progress su PG). Lo shim li
    fornisce dal messaggio Dramatiq corrente; nessun refactor dei corpi
    (che restano l'unica fonte di verita', invocata anche dai test via
    ``__wrapped__``). L'esito finale lo scrive ``_TaskResultMiddleware``.
    """
    shim = SimpleNamespace(
        request=SimpleNamespace(id=current_message_id()), name=name
    )
    wrapped = task.__wrapped__  # type: ignore[attr-defined]
    raw = getattr(wrapped, "__func__", wrapped)
    return raw(shim, **kwargs)


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
)
def cleanup_temp_files() -> dict:
    """Sweep temp file scaduti — stesso corpo del task Celery omonimo.

    Import lazy: il modulo legacy importa celery_app, che il bootstrap
    Dramatiq non deve pagare finche' l'actor non viene eseguito.
    """
    from workers.tasks.cleanup_temp_files_task import run_cleanup_temp_files

    return dict(run_cleanup_temp_files())


@dramatiq.actor(
    actor_name="workers.tasks.admin_backup_task.run_manual_backup_task",
    queue_name=queue_for(
        "workers.tasks.admin_backup_task.run_manual_backup_task"
    ),
)
def run_manual_backup_task(*, owner_token: str) -> dict:
    """Backup manuale — stesso corpo del task Celery omonimo.

    Il task_id (nel Celery: self.request.id) e' rimpiazzato dal
    message_id Dramatiq corrente. Import lazy come gli altri actor:
    il modulo legacy importa celery_app.
    """
    from workers.tasks.admin_backup_task import run_manual_backup

    return dict(
        run_manual_backup(owner_token=owner_token, task_id=current_message_id())
    )


# ---------------------------------------------------------------------------
# Task applicativi (chiusura Fase 1 → 10/10). Stessi nomi/code dei task
# Celery; il corpo condiviso e' la funzione Celery stessa, invocata con un
# self-shim (vedi _invoke_celery_task_body). Import lazy: il modulo legacy
# importa celery_app, che il bootstrap Dramatiq non deve pagare a import-time.
# ---------------------------------------------------------------------------


@dramatiq.actor(
    actor_name="carica.execute",
    queue_name=queue_for("carica.execute"),
    throws=(TaskRevoked,),
)
def carica_execute(**kwargs: object) -> object:
    """CARICA — stesso corpo del task Celery carica.execute."""
    from workers.tasks.carica_task import execute_carica_task

    return _invoke_celery_task_body(
        execute_carica_task, name="carica.execute", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="pipeline.execute",
    queue_name=queue_for("pipeline.execute"),
    throws=(TaskRevoked,),
)
def pipeline_execute(**kwargs: object) -> object:
    """PIPELINE execute — stesso corpo del task Celery pipeline.execute."""
    from workers.tasks.pipeline_task import execute_pipeline_task

    return _invoke_celery_task_body(
        execute_pipeline_task, name="pipeline.execute", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="pipeline.preview",
    queue_name=queue_for("pipeline.preview"),
    throws=(TaskRevoked,),
)
def pipeline_preview(**kwargs: object) -> object:
    """PIPELINE preview — stesso corpo del task Celery pipeline.preview."""
    from workers.tasks.pipeline_task import preview_pipeline_task

    return _invoke_celery_task_body(
        preview_pipeline_task, name="pipeline.preview", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="tasks.execute_enrichment",
    queue_name=queue_for("tasks.execute_enrichment"),
    throws=(TaskRevoked,),
)
def execute_enrichment(**kwargs: object) -> object:
    """ARRICCHISCI — stesso corpo del task Celery tasks.execute_enrichment."""
    from workers.tasks.enrichment_task import execute_enrichment_task

    return _invoke_celery_task_body(
        execute_enrichment_task, name="tasks.execute_enrichment", kwargs=kwargs
    )


@dramatiq.actor(
    actor_name="workers.tasks.schedule_task.schedule_pipeline_task",
    queue_name=queue_for("workers.tasks.schedule_task.schedule_pipeline_task"),
)
def schedule_pipeline_task(**kwargs: object) -> object:
    """Schedule pipeline — stesso corpo del task Celery omonimo."""
    from workers.tasks.schedule_task import schedule_pipeline_task as _task

    return _invoke_celery_task_body(
        _task,
        name="workers.tasks.schedule_task.schedule_pipeline_task",
        kwargs=kwargs,
    )


@dramatiq.actor(
    actor_name="workers.tasks.schedule_task.beat_heartbeat",
    queue_name=queue_for("workers.tasks.schedule_task.beat_heartbeat"),
)
def beat_heartbeat() -> object:
    """Beat heartbeat — stesso corpo del task Celery (non bind, niente self)."""
    from workers.tasks.schedule_task import beat_heartbeat as _task

    wrapped = _task.__wrapped__
    raw = getattr(wrapped, "__func__", wrapped)
    return raw()


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
