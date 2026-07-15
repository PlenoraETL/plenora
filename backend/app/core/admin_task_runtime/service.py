"""Deployment-neutral RabbitMQ/worker/task telemetry aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time

from psycopg import Error as PsycopgError
from psycopg_pool import PoolTimeout

from app.config import settings
from app.core.admin_task_runtime.models import (
    QueueStatus,
    RunningTaskStatus,
    TaskRuntimePayload,
    TaskRuntimeTotals,
    WorkerStatus,
)
from app.core.task_runtime import runtime_leases
from app.observability import runtime_ports
from app.utils.logger import get_logger


logger = get_logger(__name__)

_broker_cache_lock = threading.Lock()
_broker_cache_key: tuple[str, tuple[str, ...], int] | None = None
_broker_cache_value: tuple[tuple[object, ...], str | None] = ((), None)
_broker_cache_expires_at = 0.0


def _inspect_queues_cached(
    broker_url: str,
    queue_names: tuple[str, ...],
    *,
    timeout: int,
) -> tuple[tuple[object, ...], str | None]:
    """Bound repeated dashboard probes while preserving fresh DB leases."""
    global _broker_cache_key, _broker_cache_value, _broker_cache_expires_at
    ttl = max(1, min(int(settings.get(
        "ADMIN_TASK_RUNTIME_BROKER_CACHE_SECONDS", 5,
    )), 60))
    key = (broker_url, queue_names, timeout)
    now = time.monotonic()
    with _broker_cache_lock:
        if key == _broker_cache_key and now < _broker_cache_expires_at:
            return _broker_cache_value
        try:
            stats = tuple(runtime_ports.inspect_rabbitmq_queues(
                broker_url, queue_names, timeout=timeout,
            ))
            value = (stats, None)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            value = ((), type(exc).__name__)
            logger.warning(
                "task runtime queue probe failed: %s", type(exc).__name__,
            )
        _broker_cache_key = key
        _broker_cache_value = value
        _broker_cache_expires_at = now + ttl
        return value


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _age_seconds(now: datetime, value: datetime) -> int:
    return max(0, int((now - _utc(value)).total_seconds()))


def _active_queue_names() -> tuple[str, ...]:
    return tuple(dict.fromkeys((
        settings.TASK_QUEUE_DATA_PLANE,
        settings.TASK_QUEUE_CONTROL_PLANE,
        settings.TASK_QUEUE_SCHEDULER,
        settings.TASK_QUEUE_DEFAULT,
    )))


def get_task_runtime_payload() -> TaskRuntimePayload:
    now = datetime.now(timezone.utc)
    warnings: list[str] = []
    active_names = _active_queue_names()
    dead_letter_names = tuple(f"{name}.XQ" for name in active_names)

    queues: list[QueueStatus] = []
    broker_available = True
    broker_message = "RabbitMQ raggiungibile"
    timeout = max(1, min(int(settings.get(
        "READINESS_TASK_QUEUE_TIMEOUT_SECONDS", 2,
    )), 10))
    raw_stats, broker_error = _inspect_queues_cached(
        settings.DRAMATIQ_BROKER_URL,
        active_names + dead_letter_names,
        timeout=timeout,
    )
    if broker_error is None:
        for item in raw_stats:
            is_dead_letter = item.name in dead_letter_names
            if not item.exists:
                item_status = "error" if not is_dead_letter else "ok"
            elif is_dead_letter and item.messages > 0:
                item_status = "error"
            elif not is_dead_letter and item.consumers < 1:
                item_status = "warn"
            else:
                item_status = "ok"
            queues.append(QueueStatus(
                name=item.name,
                kind="dead_letter" if is_dead_letter else "work",
                exists=item.exists,
                messages_ready=item.messages,
                consumers=item.consumers,
                status=item_status,
            ))
    else:
        broker_available = False
        broker_message = f"RabbitMQ non interrogabile: {broker_error}"
        warnings.append(broker_message)

    max_age = max(1, int(settings.get(
        "READINESS_WORKER_HEARTBEAT_MAX_AGE_SECONDS", 45,
    )))
    workers: list[WorkerStatus] = []
    try:
        for item in runtime_leases.list_worker_heartbeats(limit=500):
            age = _age_seconds(now, item.heartbeat_at)
            workers.append(WorkerStatus(
                worker_id=item.worker_id,
                role=item.role,
                queues=list(item.queues),
                hostname=item.hostname,
                pid=item.pid,
                started_at=_utc(item.started_at),
                heartbeat_at=_utc(item.heartbeat_at),
                heartbeat_age_seconds=age,
                status="online" if age <= max_age else "stale",
            ))
    except (
        PsycopgError,
        PoolTimeout,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        warning = f"Registro worker non disponibile: {type(exc).__name__}"
        warnings.append(warning)
        logger.warning("task runtime worker probe failed: %s", exc)

    running_tasks: list[RunningTaskStatus] = []
    try:
        for item in runtime_leases.list_running_task_claims(limit=500):
            lease_remaining = int(
                (_utc(item.lease_expires_at) - now).total_seconds()
            )
            running_tasks.append(RunningTaskStatus(
                task_id=item.task_id,
                actor_name=item.actor_name,
                worker_id=item.worker_id,
                acquired_at=_utc(item.acquired_at),
                heartbeat_at=_utc(item.heartbeat_at),
                lease_expires_at=_utc(item.lease_expires_at),
                elapsed_seconds=_age_seconds(now, item.acquired_at),
                lease_remaining_seconds=lease_remaining,
                status="running" if lease_remaining > 0 else "lease_expired",
                progress=item.progress,
                message=item.message,
            ))
    except (
        PsycopgError,
        PoolTimeout,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        warning = f"Lease task non disponibili: {type(exc).__name__}"
        warnings.append(warning)
        logger.warning("task runtime claim probe failed: %s", exc)

    work_queues = [item for item in queues if item.kind == "work"]
    dead_queues = [item for item in queues if item.kind == "dead_letter"]
    totals = TaskRuntimeTotals(
        messages_ready=sum(item.messages_ready for item in work_queues),
        dead_letters=sum(item.messages_ready for item in dead_queues),
        consumers=sum(item.consumers for item in work_queues),
        workers_online=sum(item.status == "online" for item in workers),
        workers_stale=sum(item.status == "stale" for item in workers),
        running_tasks=sum(item.status == "running" for item in running_tasks),
        expired_task_leases=sum(
            item.status == "lease_expired" for item in running_tasks
        ),
    )

    has_error = (
        not broker_available
        or totals.dead_letters > 0
        or any(item.status == "error" for item in queues)
    )
    has_warning = (
        bool(warnings)
        or totals.workers_stale > 0
        or totals.expired_task_leases > 0
        or any(item.status == "warn" for item in queues)
    )
    status = "error" if has_error else "warn" if has_warning else "ok"
    return TaskRuntimePayload(
        generated_at=now,
        status=status,
        broker_available=broker_available,
        broker_message=broker_message,
        queues=queues,
        workers=workers,
        running_tasks=running_tasks,
        totals=totals,
        warnings=warnings,
    )
