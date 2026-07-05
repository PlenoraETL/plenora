"""Runtime adapter for orchestrator task backend operations."""

from __future__ import annotations

from typing import Protocol


class TaskLaunchResult(Protocol):
    id: str


class DelayTask(Protocol):
    """Handle di un task registrato.

    Il dispatch e' by-name (dispatch_task_by_name), quindi serve solo il
    `name` registrato.
    """

    name: str


class TaskRuntimeResult(Protocol):
    state: str
    info: object
    result: object
    name: str | None

    def forget(self) -> None:
        ...


class TaskRuntimePort(Protocol):
    """Porta runtime dell'orchestratore celery-free.

    Lo stato/esito vive su PostgreSQL (vedi PgResultTaskRuntime); l'annullamento
    e' cooperativo su PG (task_service.cancel_task).
    """

    def get_result(self, task_id: str) -> TaskRuntimeResult:
        ...

    def forget_result(self, task_id: str) -> None:
        ...

    def enrichment_task(self) -> DelayTask:
        ...

    def carica_task(self) -> DelayTask:
        ...

    def pipeline_task(self) -> DelayTask:
        ...

    def preview_task(self) -> DelayTask:
        ...
