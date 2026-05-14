"""IMDF publish task dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.task_runtime.dispatch import (
    TaskDispatchResult,
    TaskRuntime,
    dispatch_task_by_name,
)


@dataclass(frozen=True)
class ImdfPublishJobHandle:
    """Minimal handle returned after enqueuing an IMDF publish job."""

    id: str


class ImdfPublishTaskResult(TaskDispatchResult, Protocol):
    ...


class ImdfPublishTask(Protocol):
    def delay(
        self,
        *,
        bundle_task_id: str,
        publisher_config: dict[str, Any],
    ) -> ImdfPublishTaskResult:
        ...


class ImdfPublishRuntime(TaskRuntime, Protocol):
    def send_task(
        self,
        name: str,
        *,
        kwargs: dict[str, Any],
    ) -> ImdfPublishTaskResult:
        ...


IMDF_PUBLISH_TASK_NAME = "tasks.execute_imdf_publish"


def enqueue_imdf_publish(
    *,
    bundle_task_id: str,
    publisher_config: dict[str, Any],
    task: ImdfPublishTask | None = None,
    runtime: ImdfPublishRuntime | None = None,
) -> ImdfPublishJobHandle:
    """Enqueue IMDF publishing through the configured task runtime."""
    task_kwargs = {
        "bundle_task_id": bundle_task_id,
        "publisher_config": publisher_config,
    }

    if task is not None:
        result = task.delay(**task_kwargs)
    else:
        result = dispatch_task_by_name(
            IMDF_PUBLISH_TASK_NAME,
            runtime=runtime,
            kwargs=task_kwargs,
        )

    return ImdfPublishJobHandle(id=str(result.id))


__all__ = [
    "IMDF_PUBLISH_TASK_NAME",
    "ImdfPublishJobHandle",
    "ImdfPublishRuntime",
    "ImdfPublishTask",
    "ImdfPublishTaskResult",
    "enqueue_imdf_publish",
]
