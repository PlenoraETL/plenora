"""Bounded runtime probe for the RabbitMQ broker.

Il broker dei task e' RabbitMQ (Dramatiq) e lo stato dei task vive su
PostgreSQL (task_results). Qui resta solo la PING bounded del broker,
usata dal readiness probe (/ready).
"""

from __future__ import annotations

from typing import Any


class RabbitmqClientUnavailable(RuntimeError):
    """Raised when the RabbitMQ client library (pika) cannot be imported."""


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
            except Exception:
                # La chiusura best-effort non deve alterare l'esito del ping.
                pass


def rabbitmq_runtime_port(broker_url: str) -> RabbitmqRuntimePort:
    """Return a RabbitMQ runtime port for a broker URL."""
    return RabbitmqRuntimePort(broker_url)


def ping_rabbitmq_broker(broker_url: str, *, timeout: int) -> bool:
    """Run a bounded RabbitMQ connection probe against a broker URL."""
    return rabbitmq_runtime_port(broker_url).ping(timeout=timeout)


__all__ = [
    "RabbitmqClientUnavailable",
    "RabbitmqRuntimePort",
    "ping_rabbitmq_broker",
    "rabbitmq_runtime_port",
]
