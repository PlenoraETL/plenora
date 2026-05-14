"""Runtime probes for admin worker status."""

from __future__ import annotations

from typing import Any

from app.config import settings


class CeleryAppUnavailable(RuntimeError):
    """Raised when the Celery application cannot be imported."""


def inspect_celery_workers(
    *,
    timeout: float = 2.0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return active tasks, stats and active queues from Celery inspect."""
    try:
        from workers.celery_app import celery_app
    except Exception as exc:
        raise CeleryAppUnavailable(str(exc)) from exc

    inspect = celery_app.control.inspect(timeout=timeout)
    active = inspect.active() or {}
    stats = inspect.stats() or {}
    queues_info = inspect.active_queues() or {}
    return active, stats, queues_info


def get_celery_queue_depth(queue_name: str = "celery") -> int:
    """Return the length of the configured Celery Redis queue."""
    import redis as _redis

    redis_client = _redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2)
    return int(redis_client.llen(queue_name))


__all__ = [
    "CeleryAppUnavailable",
    "get_celery_queue_depth",
    "inspect_celery_workers",
]
