"""Bounded runtime probe for the RabbitMQ broker.

Il broker dei task e' RabbitMQ (Dramatiq) e lo stato dei task vive su
PostgreSQL (task_results). Qui resta solo la PING bounded del broker,
usata dal readiness probe (/ready).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class RabbitmqClientUnavailable(RuntimeError):
    """Raised when the RabbitMQ client library (pika) cannot be imported."""


class RabbitmqProbeError(RuntimeError):
    """Portable error raised when the AMQP client cannot inspect the broker."""


@dataclass(frozen=True)
class RabbitmqQueueStats:
    name: str
    exists: bool
    messages: int
    consumers: int


def _pika_module() -> Any:
    try:
        import pika as _pika
    except Exception as exc:
        raise RabbitmqClientUnavailable(str(exc)) from exc
    return _pika


class RabbitmqRuntimePort:
    """Bounded runtime port for the RabbitMQ broker.

    Apre una BlockingConnection pika con timeout limitato e la chiude
    subito — verifica che il broker accetti connessioni."""

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
            except _pika.exceptions.AMQPError:
                # La chiusura best-effort non deve alterare l'esito del ping.
                pass

    def queue_stats(
        self,
        queue_names: tuple[str, ...],
        *,
        timeout: int,
    ) -> list[RabbitmqQueueStats]:
        """Inspect queues through portable AMQP passive declarations."""
        _pika = _pika_module()
        params = _pika.URLParameters(self._broker_url)
        params.socket_timeout = timeout
        params.blocked_connection_timeout = timeout
        connection = _pika.BlockingConnection(params)
        stats: list[RabbitmqQueueStats] = []
        try:
            for name in queue_names:
                channel = connection.channel()
                try:
                    result = channel.queue_declare(queue=name, passive=True)
                    method = result.method
                    stats.append(
                        RabbitmqQueueStats(
                            name=name,
                            exists=True,
                            messages=int(method.message_count),
                            consumers=int(method.consumer_count),
                        )
                    )
                except _pika.exceptions.ChannelClosedByBroker as exc:
                    if int(getattr(exc, "reply_code", 0) or 0) != 404:
                        raise
                    stats.append(
                        RabbitmqQueueStats(
                            name=name,
                            exists=False,
                            messages=0,
                            consumers=0,
                        )
                    )
                finally:
                    try:
                        if channel.is_open:
                            channel.close()
                    except _pika.exceptions.AMQPError:
                        pass
        finally:
            try:
                connection.close()
            except _pika.exceptions.AMQPError:
                pass
        return stats


def rabbitmq_runtime_port(broker_url: str) -> RabbitmqRuntimePort:
    """Return a RabbitMQ runtime port for a broker URL."""
    return RabbitmqRuntimePort(broker_url)


def ping_rabbitmq_broker(broker_url: str, *, timeout: int) -> bool:
    """Run a bounded RabbitMQ connection probe against a broker URL."""
    return rabbitmq_runtime_port(broker_url).ping(timeout=timeout)


def inspect_rabbitmq_queues(
    broker_url: str,
    queue_names: tuple[str, ...],
    *,
    timeout: int,
) -> list[RabbitmqQueueStats]:
    _pika = _pika_module()
    try:
        return rabbitmq_runtime_port(broker_url).queue_stats(
            queue_names,
            timeout=timeout,
        )
    except _pika.exceptions.AMQPError as exc:
        raise RabbitmqProbeError(type(exc).__name__) from exc


__all__ = [
    "RabbitmqClientUnavailable",
    "RabbitmqProbeError",
    "RabbitmqQueueStats",
    "RabbitmqRuntimePort",
    "inspect_rabbitmq_queues",
    "ping_rabbitmq_broker",
    "rabbitmq_runtime_port",
]
