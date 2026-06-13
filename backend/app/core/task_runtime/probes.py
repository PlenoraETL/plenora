"""Bounded runtime probes for Celery and its Redis broker."""

from __future__ import annotations

from typing import Any

from app.shared.contracts.payloads import JsonObject


class CeleryAppUnavailable(RuntimeError):
    """Raised when the Celery application cannot be imported."""


class RedisClientUnavailable(RuntimeError):
    """Raised when the Redis client library cannot be imported."""


class RabbitmqClientUnavailable(RuntimeError):
    """Raised when the RabbitMQ client library (pika) cannot be imported."""


def _pika_module() -> Any:
    try:
        import pika as _pika
    except Exception as exc:
        raise RabbitmqClientUnavailable(str(exc)) from exc
    return _pika


class RabbitmqRuntimePort:
    """Bounded runtime port for the RabbitMQ broker (Plenora 2.0 Fase 5).

    Rimpiazza la PING Redis sul path di readiness: il broker e' RabbitMQ
    (Dramatiq). Apre una BlockingConnection pika con timeout limitato e la
    chiude subito — verifica che il broker accetti connessioni."""

    def __init__(self, broker_url: str) -> None:
        self._broker_url = broker_url

    def ping(self, *, timeout: int) -> bool:
        _pika = _pika_module()
        params = _pika.URLParameters(self._broker_url)
        # Bound sia il TCP handshake sia l'apertura del canale AMQP, cosi'
        # un broker che accetta la socket ma stalla non blocca /ready.
        params.socket_timeout = timeout
        params.blocked_connection_timeout = timeout
        connection = _pika.BlockingConnection(params)
        try:
            return bool(connection.is_open)
        finally:
            try:
                connection.close()
            except Exception:
                # La chiusura best-effort non deve alterare l'esito del ping.
                pass


def rabbitmq_runtime_port(broker_url: str) -> RabbitmqRuntimePort:
    """Return a RabbitMQ runtime port for a broker URL."""
    return RabbitmqRuntimePort(broker_url)


def ping_rabbitmq_broker(broker_url: str, *, timeout: int) -> bool:
    """Run a bounded RabbitMQ connection probe against a broker URL."""
    return rabbitmq_runtime_port(broker_url).ping(timeout=timeout)


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

    def info(self, *, timeout: int = 2) -> JsonObject:
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
) -> tuple[JsonObject, JsonObject, JsonObject]:
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


def get_redis_info() -> JsonObject:
    """Return Redis INFO for the configured Celery broker."""
    return get_redis_runtime_port().info()


__all__ = [
    "CeleryAppUnavailable",
    "RabbitmqClientUnavailable",
    "RabbitmqRuntimePort",
    "RedisClientUnavailable",
    "RedisRuntimePort",
    "get_celery_queue_depth",
    "get_redis_info",
    "get_redis_runtime_port",
    "inspect_celery_workers",
    "ping_rabbitmq_broker",
    "ping_redis_broker",
    "rabbitmq_runtime_port",
    "redis_runtime_port",
]
