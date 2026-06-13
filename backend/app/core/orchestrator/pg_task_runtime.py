"""Runtime di lettura stato/esito task su PostgreSQL (Plenora 2.0, F2.3).

Implementa ``TaskRuntimePort`` leggendo da ``task_result_store`` invece
che dal result backend Redis di Celery (``AsyncResult``). E' il FLIP di
lettura della Fase 2: dopo che i worker scrivono stato/progress/esito su
PG (F2.2/F2.4), l'orchestratore li rilegge da qui.

Le operazioni di BROKER (revoke, listing dei task attivi, handle dei task
per il dispatch, ``app``) restano su Celery e sono delegate a un
``CeleryTaskRuntime`` interno: la Fase 2 sposta solo lo stato/esito, non
il broker (quello e' Fase 1).

Vive nel dominio orchestrator (non in task_runtime) per non creare un
ciclo: orchestrator dipende gia' da task_runtime, l'inverso no.
"""

from __future__ import annotations

from typing import Mapping, Optional

from app.core.task_runtime import CeleryTaskRuntime, DelayTask, TaskRuntimePort
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
    """TaskRuntimePort con stato/esito su PG e broker delegato a Celery."""

    def __init__(
        self,
        *,
        broker: Optional[TaskRuntimePort] = None,
        store: Optional[TaskResultStore] = None,
    ) -> None:
        self._broker = broker or CeleryTaskRuntime()
        self._store = store or default_task_result_store()

    @property
    def app(self) -> object:
        return self._broker.app

    @app.setter
    def app(self, app: object) -> None:
        self._broker.app = app

    # --- stato/esito: PostgreSQL ---

    def get_result(self, task_id: str) -> StoredTaskResult:
        return StoredTaskResult(
            self._store.get(task_id), store=self._store, task_id=task_id
        )

    def forget_result(self, task_id: str) -> None:
        self.get_result(task_id).forget()

    # --- broker: delega a Celery (Fase 1) ---

    def revoke(self, task_id: str, *, terminate: bool = False) -> None:
        self._broker.revoke(task_id, terminate=terminate)

    def task_lists(
        self,
    ) -> tuple[
        Mapping[str, list[Mapping[str, object]]],
        Mapping[str, list[Mapping[str, object]]],
    ]:
        return self._broker.task_lists()

    def enrichment_task(self) -> DelayTask:
        return self._broker.enrichment_task()

    def carica_task(self) -> DelayTask:
        return self._broker.carica_task()

    def pipeline_task(self) -> DelayTask:
        return self._broker.pipeline_task()

    def preview_task(self) -> DelayTask:
        return self._broker.preview_task()


__all__ = ["PgResultTaskRuntime", "StoredTaskResult"]
