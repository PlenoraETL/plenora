"""Contratti del reporter stato/progress/esito task (Plenora 2.0, Fase 2).

Il reporter scrive su task_result_store keyed-by-task_id, senza dipendere
dal contesto Celery. Qui si blinda la normalizzazione della payload, la
mappatura degli stati Celery terminali e la natura best-effort (un
fallimento di scrittura non propaga). Store iniettato InMemory: lane no-DB.
"""

from __future__ import annotations

import pytest

from app.core.orchestrator.task_progress import (
    FAILURE_STATE,
    PROGRESS_STATE,
    SUCCESS_STATE,
    finalize_from_celery,
    finalize_task_result,
    report_task_progress,
)
from app.core.orchestrator.task_result_store import InMemoryTaskResultStore


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


class _RaisingStore:
    def put(self, *args, **kwargs):
        raise RuntimeError("PG giu'")

    def get(self, task_id):
        return None

    def forget(self, task_id):
        pass

    def cleanup_expired(self):
        return 0


# report_task_progress


def test_report_progress_writes_progress_state_with_normalized_payload():
    store = InMemoryTaskResultStore()
    report_task_progress(
        "t1", 42, "lavoro in corso", name="carica.execute", store=store
    )

    record = store.get("t1")
    assert record is not None
    assert record.state == PROGRESS_STATE
    assert record.name == "carica.execute"
    assert record.payload == {
        "progress": 42,
        "message": "lavoro in corso",
        "is_error": False,
    }


def test_report_progress_negative_percent_flags_error_and_clamps_to_zero():
    store = InMemoryTaskResultStore()
    report_task_progress("t1", -1, "errore", store=store)

    record = store.get("t1")
    assert record is not None
    assert record.payload == {"progress": 0, "message": "errore", "is_error": True}


def test_report_progress_without_task_id_is_noop():
    store = InMemoryTaskResultStore()
    report_task_progress(None, 10, "x", store=store)
    report_task_progress("", 10, "x", store=store)
    # Nessuna riga scritta.
    assert store.get("") is None


def test_report_progress_is_best_effort_on_store_failure():
    # Non deve propagare: la scrittura progress non puo' far fallire il task.
    report_task_progress("t1", 10, "x", store=_RaisingStore())


# finalize_task_result


def test_finalize_writes_terminal_state_and_payload():
    store = InMemoryTaskResultStore()
    finalize_task_result(
        "t1",
        state=SUCCESS_STATE,
        payload={"success": True, "rows_written": 7},
        name="carica.execute",
        store=store,
    )

    record = store.get("t1")
    assert record is not None
    assert record.state == SUCCESS_STATE
    assert record.payload == {"success": True, "rows_written": 7}


def test_finalize_is_best_effort_on_store_failure():
    finalize_task_result(
        "t1", state=SUCCESS_STATE, payload={"success": True}, store=_RaisingStore()
    )


# finalize_from_celery (adapter Task.after_return)


def test_finalize_from_celery_success_stores_return_dict_as_payload():
    store = InMemoryTaskResultStore()
    finalize_from_celery(
        "t1", "SUCCESS", {"success": True, "rows_written": 3}, None,
        name="carica.execute", store=store,
    )

    record = store.get("t1")
    assert record is not None
    assert record.state == SUCCESS_STATE
    assert record.payload == {"success": True, "rows_written": 3}


def test_finalize_from_celery_wraps_non_dict_return():
    store = InMemoryTaskResultStore()
    finalize_from_celery("t1", "SUCCESS", "pong", None, store=store)

    record = store.get("t1")
    assert record is not None
    assert record.payload == {"result": "pong"}


def test_finalize_from_celery_failure_is_sanitized():
    store = InMemoryTaskResultStore()

    class _Einfo:
        exception = ValueError("dettaglio interno da non esporre")

    finalize_from_celery("t1", "FAILURE", None, _Einfo(), store=store)

    record = store.get("t1")
    assert record is not None
    assert record.state == FAILURE_STATE
    assert record.payload == {"success": False, "error": "ValueError"}
    # Nessun str(exc) raw nella payload (canale letto dal FE).
    assert "dettaglio interno" not in str(record.payload)


@pytest.mark.parametrize("status", ["RETRY", "REVOKED", "STARTED", "PENDING"])
def test_finalize_from_celery_skips_non_terminal_states(status):
    store = InMemoryTaskResultStore()
    finalize_from_celery("t1", status, {"x": 1}, None, store=store)
    # Stati non terminali: nessuna scrittura.
    assert store.get("t1") is None
