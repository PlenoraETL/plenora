"""Scheduler job dispatch boundary.

This module keeps scheduler application code independent from the concrete
background task runtime. The default implementation still uses Celery, but the
calling code only depends on the small protocol below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


SCHEDULE_PIPELINE_TASK_NAME = "workers.tasks.schedule_task.schedule_pipeline_task"


@dataclass(frozen=True)
class ScheduledJobHandle:
    """Minimal handle returned after enqueuing a scheduled run."""

    id: str


class ScheduledJobResult(Protocol):
    """Minimal async result contract exposed by the runtime adapter."""

    id: str


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


def scheduled_run_kwargs(
    *,
    triggered_by: str,
    retry_attempt: int = 0,
    scheduled_for: str | None = None,
) -> dict[str, Any]:
    """Return the runtime kwargs for a scheduled pipeline task."""
    dispatch_kwargs: dict[str, Any] = {
        "triggered_by": triggered_by,
        "retry_attempt": retry_attempt,
    }
    if scheduled_for:
        dispatch_kwargs["scheduled_for"] = scheduled_for
    return dispatch_kwargs


def enqueue_schedule_run(
    schedule_id: str,
    *,
    triggered_by: str,
    retry_attempt: int,
    scheduled_for: str | None = None,
    countdown: int | float | None = None,
    task: ScheduledJobTask | None = None,
) -> ScheduledJobHandle:
    """Enqueue a scheduler run through the configured task runtime."""
    if task is None:
        from workers.tasks.schedule_task import schedule_pipeline_task

        task = schedule_pipeline_task

    apply_kwargs: dict[str, Any] = {
        "args": [schedule_id],
        "kwargs": scheduled_run_kwargs(
            triggered_by=triggered_by,
            retry_attempt=retry_attempt,
            scheduled_for=scheduled_for,
        ),
    }
    if countdown is not None:
        apply_kwargs["countdown"] = countdown

    result = task.apply_async(**apply_kwargs)
    return ScheduledJobHandle(id=str(result.id))


__all__ = [
    "SCHEDULE_PIPELINE_TASK_NAME",
    "ScheduledJobHandle",
    "ScheduledJobResult",
    "ScheduledJobTask",
    "enqueue_schedule_run",
    "scheduled_run_kwargs",
]
