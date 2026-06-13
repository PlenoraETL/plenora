"""Contratti del runtime di lettura stato task su PG (Plenora 2.0).

L'orchestratore legge stato/esito da task_result_store (PG). StoredTaskResult
riproduce la semantica storica ``AsyncResult`` (info==result==payload, riga
assente -> PENDING). Plenora 2.0 Fase 5 (F5.6): il runtime e' celery-free —
niente piu' delega broker (revoke/task_lists/app rimossi); i metodi ``*_task()``
ritornano le funzioni-corpo dei task (con attributo ``name``). Store InMemory:
lane no-DB.
"""

from __future__ import annotations

import pytest

from app.core.orchestrator.pg_task_runtime import (
    PgResultTaskRuntime,
    StoredTaskResult,
)
from app.core.orchestrator.task_result_store import InMemoryTaskResultStore


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


# StoredTaskResult — semantica AsyncResult


def test_stored_result_exposes_payload_as_info_and_result():
    store = InMemoryTaskResultStore()
    store.put(
        "t1", state="SUCCESS",
        payload={"success": True, "result_id": "r1"},
        name="pipeline.execute",
    )
    record = store.get("t1")

    sr = StoredTaskResult(record, store=store, task_id="t1")
    assert sr.state == "SUCCESS"
    assert sr.name == "pipeline.execute"
    assert sr.info == {"success": True, "result_id": "r1"}
    assert sr.result == sr.info  # semantica Celery info == result


def test_stored_result_missing_row_is_pending_with_no_info():
    sr = StoredTaskResult(None, store=InMemoryTaskResultStore(), task_id="ignoto")
    assert sr.state == "PENDING"
    assert sr.info is None
    assert sr.result is None
    assert sr.name is None


def test_stored_result_forget_removes_from_store():
    store = InMemoryTaskResultStore()
    store.put("t1", state="SUCCESS", payload={"success": True})
    StoredTaskResult(store.get("t1"), store=store, task_id="t1").forget()
    assert store.get("t1") is None


# PgResultTaskRuntime — read da PG


def test_get_result_reads_from_store():
    store = InMemoryTaskResultStore()
    store.put("t1", state="PROGRESS", payload={"progress": 30}, name="carica.execute")
    runtime = PgResultTaskRuntime(store=store)

    result = runtime.get_result("t1")
    assert result.state == "PROGRESS"
    assert result.info == {"progress": 30}
    assert result.name == "carica.execute"


def test_get_result_unknown_task_is_pending():
    runtime = PgResultTaskRuntime(store=InMemoryTaskResultStore())
    assert runtime.get_result("ignoto").state == "PENDING"


def test_forget_result_deletes_from_store():
    store = InMemoryTaskResultStore()
    store.put("t1", state="SUCCESS", payload={"success": True})
    PgResultTaskRuntime(store=store).forget_result("t1")
    assert store.get("t1") is None


def test_task_handles_return_named_task_bodies():
    """I metodi *_task() ritornano le funzioni-corpo con il nome registrato
    (usato dal dispatch by-name di submit_task), senza Celery."""
    runtime = PgResultTaskRuntime(store=InMemoryTaskResultStore())

    assert runtime.carica_task().name == "carica.execute"
    assert runtime.pipeline_task().name == "pipeline.execute"
    assert runtime.preview_task().name == "pipeline.preview"
    assert runtime.enrichment_task().name == "tasks.execute_enrichment"


def test_runtime_has_no_broker_delegation():
    """Plenora 2.0 Fase 5: niente piu' revoke/task_lists/app (delega Celery)."""
    runtime = PgResultTaskRuntime(store=InMemoryTaskResultStore())
    assert not hasattr(runtime, "revoke")
    assert not hasattr(runtime, "task_lists")
    assert not hasattr(runtime, "app")
