"""Runtime di lettura stato/esito task su PostgreSQL (Plenora 2.0).

Implementa ``TaskRuntimePort`` leggendo stato/progress/esito da
``task_result_store`` (PG), dove li scrivono i worker Dramatiq. I metodi
``*_task()`` ritornano le funzioni-corpo dei task (con attributo ``name``)
per il dispatch by-name di ``submit_task``.

Plenora 2.0 Fase 5 (F5.6): niente piu' delega a Celery. Lo stato non e' piu'
il result backend Redis (``AsyncResult``); il revoke e' cooperativo su PG
(vedi task_service.cancel_task + revocation_store); l'inspect dei worker
Celery e' stato rimosso (osservabilita' admin derivata da PG).

Vive nel dominio orchestrator (non in task_runtime) per non creare un
ciclo: orchestrator dipende gia' da task_runtime, l'inverso no.
"""

from __future__ import annotations

from typing import Optional

from app.core.task_runtime import DelayTask
from app.core.orchestrator.task_result_store import (
    PENDING_STATE,
    TaskResultRecord,
    TaskResultStore,
    default_task_result_store,
)


class StoredTaskResult:
    """``TaskRuntimeResult`` letto da task_result_store.

    Riproduce la semantica Celery ``AsyncResult``: ``info == result ==
    payload``; un task sconosciuto (riga assente o scaduta) e' ``PENDING``
    con ``info``/``result`` a None.
    """

    def __init__(
        self,
        record: Optional[TaskResultRecord],
        *,
        store: TaskResultStore,
        task_id: str,
    ) -> None:
        self._record = record
        self._store = store
        self._task_id = task_id

    @property
    def state(self) -> str:
        return self._record.state if self._record is not None else PENDING_STATE

    @property
    def info(self) -> object:
        return self._record.payload if self._record is not None else None

    @property
    def result(self) -> object:
        return self._record.payload if self._record is not None else None

    @property
    def name(self) -> Optional[str]:
        return self._record.name if self._record is not None else None

    def forget(self) -> None:
        self._store.forget(self._task_id)


class PgResultTaskRuntime:
    """TaskRuntimePort con stato/esito su PostgreSQL (Plenora 2.0, celery-free)."""

    def __init__(
        self,
        *,
        store: Optional[TaskResultStore] = None,
    ) -> None:
        self._store = store or default_task_result_store()

    # --- stato/esito: PostgreSQL ---

    def get_result(self, task_id: str) -> StoredTaskResult:
        return StoredTaskResult(
            self._store.get(task_id), store=self._store, task_id=task_id
        )

    def forget_result(self, task_id: str) -> None:
        self.get_result(task_id).forget()

    # --- handle dei task per il dispatch by-name (espongono `name`) ---
    # Import lazy: evita un edge top-level orchestrator->workers nel grafo e
    # tiene leggero l'import dell'orchestratore.

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


__all__ = ["PgResultTaskRuntime", "StoredTaskResult"]
