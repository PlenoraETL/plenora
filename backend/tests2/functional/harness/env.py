"""Environment helpers for functional/integration subprocess fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, MutableMapping
from urllib.parse import urlsplit, urlunsplit


_BROKER_ENV_NAMES = (
    "CELERY_BROKER_URL",
    "REDIS_URL",
    "PLENORA_INSTALL_REDIS_URL",
)
_RESULT_ENV_NAMES = (
    "CELERY_RESULT_BACKEND",
    "CELERY_BROKER_URL",
    "REDIS_URL",
    "PLENORA_INSTALL_REDIS_URL",
)
_HOST_RUN_BROKER_URL = "redis://127.0.0.1:6379/0"
_HOST_RUN_RESULT_BACKEND = "redis://127.0.0.1:6379/1"


def _first_env_value(
    env: Mapping[str, str],
    names: tuple[str, ...],
) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def configure_redis_urls(env: MutableMapping[str, str]) -> None:
    """Populate canonical Celery Redis URLs for test subprocesses.

    Precedence is env-first so Docker/container runs can point at service
    hostnames, e.g. ``CELERY_BROKER_URL=redis://redis:6379/0``. The
    localhost fallback is only for host-run tests against published Docker
    ports.
    """
    broker_url = _first_env_value(env, _BROKER_ENV_NAMES)
    result_backend = _first_env_value(env, _RESULT_ENV_NAMES)

    if not broker_url and _running_in_container():
        raise RuntimeError(
            "Integration/functional Redis URL is not configured. "
            "Inside a container, set CELERY_BROKER_URL or REDIS_URL "
            "to a service hostname such as redis://redis:6379/0. "
            "The 127.0.0.1 fallback is reserved for host-run tests "
            "against published Docker ports."
        )

    env["CELERY_BROKER_URL"] = broker_url or _HOST_RUN_BROKER_URL
    env["CELERY_RESULT_BACKEND"] = (
        result_backend or _HOST_RUN_RESULT_BACKEND
    )
    # Plenora 2.0 Fase 5: il worker dei test e' Dramatiq su RabbitMQ -> ha
    # bisogno del broker amqp. Default host-run; in container l'env (es.
    # DRAMATIQ_BROKER_URL=amqp://guest:guest@rabbitmq:5672) lo sovrascrive.
    env.setdefault("DRAMATIQ_BROKER_URL", "amqp://guest:guest@127.0.0.1:5672")


def isolate_redis_dbs(
    env: MutableMapping[str, str],
    *,
    broker_db: int,
    result_db: int,
) -> None:
    """Move Celery test subprocesses off the application worker DBs.

    Functional/integration tests spawn their own worker. If they reuse DB 0,
    an already-running compose worker can consume the task first, bypassing
    the test env and the current working tree code.
    """

    def _with_db(url: str, db: int) -> str:
        parts = urlsplit(url)
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment)
        )

    env["CELERY_BROKER_URL"] = _with_db(env["CELERY_BROKER_URL"], broker_db)
    env["CELERY_RESULT_BACKEND"] = _with_db(
        env["CELERY_RESULT_BACKEND"], result_db
    )
