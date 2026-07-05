"""Scheduler job dispatch boundary.

This module keeps scheduler application code independent from the concrete
background task runtime. The default implementation still uses Celery, but the
calling code only depends on the small protocol below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from typing_extensions import TypedDict

from app.core.task_runtime.dispatch import (
    TaskDispatchResult,
    TaskRuntime,
    dispatch_task_by_name,
)


SCHEDULE_PIPELINE_TASK_NAME = "workers.tasks.schedule_task.schedule_pipeline_task"


@dataclass(frozen=True)
class ScheduledJobHandle:
    """Minimal handle returned after enqueuing a scheduled run."""

    id: str


class ScheduledJobResult(TaskDispatchResult, Protocol):
    """Minimal async result contract exposed by the runtime adapter."""


class ScheduledJobTask(Protocol):
    """Minimal task contract required to enqueue a scheduler run."""

    def apply_async(
        self,
        *,
        args: list[str],
        kwargs: dict[str, Any],
        countdown: int | float | None = None,
    ) -> ScheduledJobResult:
        ...


class ScheduledJobRuntime(TaskRuntime, Protocol):
    """Minimal queue runtime contract required to enqueue a scheduler run."""

    def send_task(
        self,
        name: str,
        *,
        args: list[str],
        kwargs: dict[str, Any],
        countdown: int | float | None = None,
    ) -> ScheduledJobResult:
        ...


class ScheduledRunKwargs(TypedDict, total=False):
    triggered_by: str
    retry_attempt: int
    scheduled_for: str


def scheduled_run_kwargs(
    *,
    triggered_by: str,
    retry_attempt: int = 0,
    scheduled_for: str | None = None,
) -> ScheduledRunKwargs:
    """Return the runtime kwargs for a scheduled pipeline task.

    `scheduled_for` deve essere SEMPRE valorizzato a dispatch-time:
    fissarlo qui (e non lazy nel task) garantisce che retry Celery
    dello stesso task vedano lo stesso timestamp, e quindi la chiave
    idempotency CARICA (`schedule-{id}-{scheduled_for}-carica`) resti
    stabile. Pre-fix il task ricalcolava `datetime.now()` ad ogni
    invocazione → chiave diversa ad ogni retry → scritture duplicate.
    """
    if scheduled_for is None:
        scheduled_for = datetime.now(timezone.utc).isoformat()
    dispatch_kwargs: ScheduledRunKwargs = {
        "triggered_by": triggered_by,
        "retry_attempt": retry_attempt,
        "scheduled_for": scheduled_for,
    }
    return dispatch_kwargs


def enqueue_schedule_run(
    schedule_id: str,
    *,
    triggered_by: str,
    retry_attempt: int,
    scheduled_for: str | None = None,
    countdown: int | float | None = None,
    task: ScheduledJobTask | None = None,
    runtime: ScheduledJobRuntime | None = None,
) -> ScheduledJobHandle:
    """Enqueue a scheduler run through the configured task runtime."""
    task_kwargs = dict(
        scheduled_run_kwargs(
            triggered_by=triggered_by,
            retry_attempt=retry_attempt,
            scheduled_for=scheduled_for,
        )
    )
    task_kwargs["schedule_id"] = schedule_id
    apply_kwargs: dict[str, Any] = {"args": [], "kwargs": task_kwargs}
    if countdown is not None:
        apply_kwargs["countdown"] = countdown

    if task is not None:
        result = task.apply_async(**apply_kwargs)
    else:
        result = dispatch_task_by_name(
            SCHEDULE_PIPELINE_TASK_NAME,
            runtime=runtime,
            **apply_kwargs,
        )

    return ScheduledJobHandle(id=str(result.id))


__all__ = [
    "SCHEDULE_PIPELINE_TASK_NAME",
    "ScheduledJobHandle",
    "ScheduledJobResult",
    "ScheduledJobRuntime",
    "ScheduledJobTask",
    "enqueue_schedule_run",
    "scheduled_run_kwargs",
]
