"""Contratti del runtime di lettura stato task su PG (Plenora 2.0, F2.3).

Il FLIP di lettura: l'orchestratore legge stato/esito da task_result_store
invece che da AsyncResult Celery. StoredTaskResult riproduce la semantica
``AsyncResult`` (info==result==payload, riga assente -> PENDING). Le
operazioni broker restano delegate a Celery. Store InMemory: lane no-DB.
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


class _FakeBroker:
    def __init__(self) -> None:
        self.app = object()
        self.revoked: list[tuple] = []
        self.handles = {
            "enrichment": object(),
            "carica": object(),
            "pipeline": object(),
            "preview": object(),
        }

    def revoke(self, task_id, *, terminate=False):
        self.revoked.append((task_id, terminate))

    def task_lists(self):
        return ({"w": [{"id": "a"}]}, {"w": [{"id": "b"}]})

    def enrichment_task(self):
        return self.handles["enrichment"]

    def carica_task(self):
        return self.handles["carica"]

    def pipeline_task(self):
        return self.handles["pipeline"]

    def preview_task(self):
        return self.handles["preview"]


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


# PgResultTaskRuntime — read da PG, broker delegato


def test_get_result_reads_from_store():
    store = InMemoryTaskResultStore()
    store.put("t1", state="PROGRESS", payload={"progress": 30}, name="carica.execute")
    runtime = PgResultTaskRuntime(broker=_FakeBroker(), store=store)

    result = runtime.get_result("t1")
    assert result.state == "PROGRESS"
    assert result.info == {"progress": 30}
    assert result.name == "carica.execute"


def test_get_result_unknown_task_is_pending():
    runtime = PgResultTaskRuntime(broker=_FakeBroker(), store=InMemoryTaskResultStore())
    assert runtime.get_result("ignoto").state == "PENDING"


def test_forget_result_deletes_from_store():
    store = InMemoryTaskResultStore()
    store.put("t1", state="SUCCESS", payload={"success": True})
    PgResultTaskRuntime(broker=_FakeBroker(), store=store).forget_result("t1")
    assert store.get("t1") is None


def test_broker_operations_delegate_to_celery_runtime():
    broker = _FakeBroker()
    runtime = PgResultTaskRuntime(broker=broker, store=InMemoryTaskResultStore())

    runtime.revoke("t1", terminate=True)
    assert broker.revoked == [("t1", True)]

    active, reserved = runtime.task_lists()
    assert active == {"w": [{"id": "a"}]}
    assert reserved == {"w": [{"id": "b"}]}

    assert runtime.enrichment_task() is broker.handles["enrichment"]
    assert runtime.carica_task() is broker.handles["carica"]
    assert runtime.pipeline_task() is broker.handles["pipeline"]
    assert runtime.preview_task() is broker.handles["preview"]


def test_app_property_delegates_to_broker():
    broker = _FakeBroker()
    runtime = PgResultTaskRuntime(broker=broker, store=InMemoryTaskResultStore())
    assert runtime.app is broker.app

    sentinel = object()
    runtime.app = sentinel
    assert broker.app is sentinel
