"""Environment helpers for functional/integration subprocess fixtures.

Plenora 2.0: il broker dei task e' RabbitMQ (Dramatiq). L'isolamento fra
i worker spawnati dai test e un eventuale worker compose gia' in ascolto
NON usa piu' i DB Redis separati (concetto senza equivalente AMQP): ogni
env di test riceve CODE DEDICATE suffissate (``TASK_QUEUE_*``). Worker e
backend subprocess condividono lo stesso env, quindi dispatchano e
consumano sulle stesse code suffissate; un worker compose sulle code
default non vede mai i task di test. Le code abbandonate da run
precedenti restano sul broker come garbage innocuo (nessun consumer).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Mapping, MutableMapping, Optional


_BROKER_ENV_NAMES = ("DRAMATIQ_BROKER_URL",)
_HOST_RUN_BROKER_URL = "amqp://guest:guest@127.0.0.1:5672"

# Nome canonico -> base name della coda (stessi default di
# Settings.DEFAULTS; il suffisso li rende privati del run di test).
_TASK_QUEUE_BASES: Mapping[str, str] = {
    "TASK_QUEUE_DEFAULT": "default",
    "TASK_QUEUE_DATA_PLANE": "data-plane",
    "TASK_QUEUE_CONTROL_PLANE": "control-plane",
    "TASK_QUEUE_SCHEDULER": "scheduler",
}


def _first_env_value(
    env: Mapping[str, str],
    names: tuple[str, ...],
) -> Optional[str]:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def configure_broker_env(env: MutableMapping[str, str]) -> None:
    """Populate the RabbitMQ broker URL for test subprocesses.

    Precedence is env-first so Docker/container runs can point at service
    hostnames, e.g. ``DRAMATIQ_BROKER_URL=amqp://guest:guest@rabbitmq:5672``.
    The localhost fallback is only for host-run tests against published
    Docker ports.
    """
    broker_url = _first_env_value(env, _BROKER_ENV_NAMES)

    if not broker_url and _running_in_container():
        raise RuntimeError(
            "Functional/integration broker URL is not configured. "
            "Inside a container, set DRAMATIQ_BROKER_URL to a service "
            "hostname such as amqp://guest:guest@rabbitmq:5672. "
            "The 127.0.0.1 fallback is reserved for host-run tests "
            "against published Docker ports."
        )

    env["DRAMATIQ_BROKER_URL"] = broker_url or _HOST_RUN_BROKER_URL


def isolate_task_queues(
    env: MutableMapping[str, str],
    *,
    suffix: Optional[str] = None,
) -> str:
    """Dedica al run di test un set di code suffissate.

    Rimpiazza il vecchio ``isolate_redis_dbs`` (DB 11-14): su RabbitMQ
    l'isolamento e' per NOME di coda. Il worker spawnato dichiara le code
    suffissate e il backend subprocess (stesso env) vi instrada i task:
    un worker compose sulle code default non puo' rubarli, e i task
    orfani di run morti restano su code senza consumer.

    Ritorna il suffisso applicato (utile per logging/diagnostica).
    """
    if suffix is None:
        suffix = uuid.uuid4().hex[:8]
    for key, base in _TASK_QUEUE_BASES.items():
        env[key] = f"{base}-t{suffix}"
    return suffix
