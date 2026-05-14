"""IMDF publish task dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ImdfPublishJobHandle:
    """Minimal handle returned after enqueuing an IMDF publish job."""

    id: str


class ImdfPublishTaskResult(Protocol):
    id: str


class ImdfPublishTask(Protocol):
    def delay(
        self,
        *,
        bundle_task_id: str,
        publisher_config: dict[str, Any],
    ) -> ImdfPublishTaskResult:
        ...


def enqueue_imdf_publish(
    *,
    bundle_task_id: str,
    publisher_config: dict[str, Any],
    task: ImdfPublishTask | None = None,
) -> ImdfPublishJobHandle:
    """Enqueue IMDF publishing through the configured task runtime."""
    if task is None:
        from workers.tasks.imdf_publish_task import execute_imdf_publish_task

        task = execute_imdf_publish_task

    result = task.delay(
        bundle_task_id=bundle_task_id,
        publisher_config=publisher_config,
    )
    return ImdfPublishJobHandle(id=str(result.id))


__all__ = [
    "ImdfPublishJobHandle",
    "ImdfPublishTask",
    "ImdfPublishTaskResult",
    "enqueue_imdf_publish",
]
