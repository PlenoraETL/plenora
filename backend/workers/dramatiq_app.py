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

import dramatiq
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware import CurrentMessage

from app.config import settings

# Nessun path nell'URL = vhost di default "/" (un trailing "//" verrebbe
# interpretato da pika come vhost vuoto -> 530 NOT_ALLOWED).
_DEFAULT_BROKER_URL = "amqp://guest:guest@rabbitmq:5672"


def broker_url() -> str:
    return os.environ.get("DRAMATIQ_BROKER_URL", _DEFAULT_BROKER_URL)


broker = RabbitmqBroker(url=broker_url())
# CurrentMessage espone il messaggio in esecuzione all'actor: e' il
# rimpiazzo Dramatiq di `self.request.id` (bind=True) di Celery, usato
# dai task che mettono il task_id nel result payload (admin_backup; in
# Fase 2 anche carica/pipeline).
broker.add_middleware(CurrentMessage())
dramatiq.set_broker(broker)


def current_message_id() -> str | None:
    """message_id del messaggio Dramatiq in esecuzione, o None fuori da un
    actor. Equivalente di `self.request.id` lato Celery."""
    message = CurrentMessage.get_current_message()
    return message.message_id if message is not None else None


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


__all__ = [
    "TASK_QUEUE_ROUTES",
    "broker",
    "broker_url",
    "cleanroom_beat_ping",
    "cleanroom_ping",
    "cleanup_temp_files",
    "current_message_id",
    "queue_for",
    "run_manual_backup_task",
]
