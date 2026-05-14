"""Bounded runtime probes for Celery and its Redis broker."""

from __future__ import annotations

from typing import Any


class CeleryAppUnavailable(RuntimeError):
    """Raised when the Celery application cannot be imported."""


class RedisClientUnavailable(RuntimeError):
    """Raised when the Redis client library cannot be imported."""


def _redis_module() -> Any:
    try:
        import redis as _redis
    except Exception as exc:
        raise RedisClientUnavailable(str(exc)) from exc
    return _redis


class RedisRuntimePort:
    """Small runtime port for Redis broker probes."""

    def __init__(self, broker_url: str) -> None:
        self._broker_url = broker_url

    def ping(self, *, timeout: int) -> bool:
        """Run a bounded Redis PING against the broker."""
        _redis = _redis_module()
        client = _redis.from_url(
            self._broker_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        return bool(client.ping())

    def queue_depth(self, queue_name: str = "celery", *, timeout: int = 2) -> int:
        """Return the Redis list length for a Celery queue."""
        _redis = _redis_module()
        redis_client = _redis.from_url(
            self._broker_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        return int(redis_client.llen(queue_name))

    def info(self, *, timeout: int = 2) -> dict[str, Any]:
        """Return Redis INFO for the broker."""
        _redis = _redis_module()
        client = _redis.Redis.from_url(
            self._broker_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        return dict(client.info())


def redis_runtime_port(broker_url: str) -> RedisRuntimePort:
    """Return a Redis runtime port for a broker URL."""
    return RedisRuntimePort(broker_url)


def ping_redis_broker(redis_url: str, *, timeout: int) -> bool:
    """Run a bounded Redis PING against a broker URL."""
    return redis_runtime_port(redis_url).ping(timeout=timeout)


def get_redis_runtime_port() -> RedisRuntimePort:
    """Return the Redis runtime port bound to the configured broker URL."""
    from app.config import settings

    return redis_runtime_port(settings.CELERY_BROKER_URL)


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
    return get_redis_runtime_port().queue_depth(queue_name)


def get_redis_info() -> dict[str, Any]:
    """Return Redis INFO for the configured Celery broker."""
    return get_redis_runtime_port().info()


__all__ = [
    "CeleryAppUnavailable",
    "RedisClientUnavailable",
    "RedisRuntimePort",
    "get_celery_queue_depth",
    "get_redis_info",
    "get_redis_runtime_port",
    "inspect_celery_workers",
    "ping_redis_broker",
    "redis_runtime_port",
]
