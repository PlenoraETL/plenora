"""Runtime ports for bounded observability dependency probes."""

from __future__ import annotations

from typing import Any

from app.core.task_runtime.probes import ping_rabbitmq_broker


def bounded_readiness_timeout(settings: Any, key: str, default: int = 2) -> int:
    """Return a positive readiness timeout, falling back to default."""
    try:
        timeout = int(settings.get(key, default))
    except Exception:
        timeout = default
    if timeout <= 0:
        return default
    return timeout


__all__ = [
    "bounded_readiness_timeout",
    "ping_rabbitmq_broker",
]
