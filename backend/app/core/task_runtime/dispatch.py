"""Shared task dispatch adapter for application boundaries."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskDispatchResult(Protocol):
    """Minimal async result contract exposed by task runtimes."""

    id: str


@runtime_checkable
class TaskRuntime(Protocol):
    """Minimal queue runtime contract for named task dispatch."""

    def send_task(self, name: str, **options: Any) -> TaskDispatchResult:
        ...


def default_task_runtime() -> TaskRuntime:
    """Return the process-wide concrete task runtime.

    Plenora 2.0 Fase 1 (cutover): il runtime di dispatch di default e'
    Dramatiq su RabbitMQ (non piu' Celery su Redis). L'import di
    workers.dramatiq_app registra il broker globale (set_broker) usato da
    DramatiqTaskRuntime. Vale per OGNI processo che dispatcha (API, beat,
    worker): RabbitMQ deve essere raggiungibile."""
    import workers.dramatiq_app  # noqa: F401 — side-effect: dramatiq.set_broker

    from app.core.task_runtime.dramatiq_runtime import DramatiqTaskRuntime

    return DramatiqTaskRuntime()


def dispatch_task_by_name(
    name: str,
    *,
    runtime: TaskRuntime | None = None,
    **options: Any,
) -> TaskDispatchResult:
    """Dispatch a named task through the provided or default runtime."""
    task_runtime = runtime if runtime is not None else default_task_runtime()
    return task_runtime.send_task(name, **options)


__all__ = [
    "TaskDispatchResult",
    "TaskRuntime",
    "default_task_runtime",
    "dispatch_task_by_name",
]
