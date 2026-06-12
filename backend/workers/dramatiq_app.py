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

from app.config import settings

# Nessun path nell'URL = vhost di default "/" (un trailing "//" verrebbe
# interpretato da pika come vhost vuoto -> 530 NOT_ALLOWED).
_DEFAULT_BROKER_URL = "amqp://guest:guest@rabbitmq:5672"


def broker_url() -> str:
    return os.environ.get("DRAMATIQ_BROKER_URL", _DEFAULT_BROKER_URL)


broker = RabbitmqBroker(url=broker_url())
dramatiq.set_broker(broker)


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


__all__ = ["TASK_QUEUE_ROUTES", "broker", "broker_url", "cleanroom_ping", "queue_for"]
