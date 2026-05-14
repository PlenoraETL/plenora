"""Runtime adapter for orchestrator task backend operations."""

from __future__ import annotations

from typing import Mapping, Protocol

from celery.result import AsyncResult
from workers.celery_app import celery_app


class TaskLaunchResult(Protocol):
    id: str


class DelayTask(Protocol):
    def delay(self, **kwargs: object) -> TaskLaunchResult:
        ...


class TaskRuntimeResult(Protocol):
    state: str
    info: object
    result: object
    name: str | None

    def forget(self) -> None:
        ...


class TaskRuntimePort(Protocol):
    app: object

    def get_result(self, task_id: str) -> TaskRuntimeResult:
        ...

    def forget_result(self, task_id: str) -> None:
        ...

    def revoke(self, task_id: str, *, terminate: bool = False) -> None:
        ...

    def task_lists(
        self,
    ) -> tuple[
        Mapping[str, list[Mapping[str, object]]],
        Mapping[str, list[Mapping[str, object]]],
    ]:
        ...

    def enrichment_task(self) -> DelayTask:
        ...

    def carica_task(self) -> DelayTask:
        ...

    def pipeline_task(self) -> DelayTask:
        ...

    def preview_task(self) -> DelayTask:
        ...


class CeleryTaskRuntime:
    """Celery-backed implementation of the orchestrator task runtime port."""

    def __init__(self, app: object = celery_app):
        self.app = app

    def get_result(self, task_id: str) -> TaskRuntimeResult:
        return AsyncResult(task_id, app=self.app)

    def forget_result(self, task_id: str) -> None:
        self.get_result(task_id).forget()

    def revoke(self, task_id: str, *, terminate: bool = False) -> None:
        self.app.control.revoke(task_id, terminate=terminate)  # type: ignore[attr-defined]

    def task_lists(
        self,
    ) -> tuple[
        Mapping[str, list[Mapping[str, object]]],
        Mapping[str, list[Mapping[str, object]]],
    ]:
        inspect = self.app.control.inspect()  # type: ignore[attr-defined]
        return inspect.active() or {}, inspect.reserved() or {}

    def enrichment_task(self) -> DelayTask:
        from workers.tasks.enrichment_task import execute_enrichment_task

        return execute_enrichment_task

    def carica_task(self) -> DelayTask:
        from workers.tasks.carica_task import execute_carica_task

        return execute_carica_task

    def pipeline_task(self) -> DelayTask:
        from workers.tasks.pipeline_task import execute_pipeline_task

        return execute_pipeline_task

    def preview_task(self) -> DelayTask:
        from workers.tasks.pipeline_task import preview_pipeline_task

        return preview_pipeline_task
