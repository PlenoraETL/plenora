"""Contratti del reporter stato/progress/esito task (Plenora 2.0, Fase 2).

Il reporter scrive su task_result_store keyed-by-task_id, senza dipendere
dal contesto Celery. Qui si blinda la normalizzazione della payload, la
mappatura degli stati Celery terminali e la natura best-effort (un
fallimento di scrittura non propaga). Store iniettato InMemory: lane no-DB.
"""

from __future__ import annotations

import pytest

from app.core.orchestrator import task_progress as task_progress_module
from app.core.orchestrator.task_progress import (
    FAILURE_STATE,
    PROGRESS_STATE,
    REVOKED_STATE,
    SUCCESS_STATE,
    TaskRevoked,
    finalize_from_celery,
    finalize_revoked,
    finalize_task_result,
    raise_if_task_revoked,
    report_task_progress,
)
from app.core.orchestrator.task_result_store import InMemoryTaskResultStore
from app.core.orchestrator.task_revocation_store import InMemoryTaskRevocationStore


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


@pytest.fixture(autouse=True)
def _reset_revocation_store():
    # Default lane-safe: store in-memory vuoto (nessun task revocato, niente
    # tentativo di lettura PG). I test sull'annullamento iniettano un store
    # con il flag via _set_revocation_store_for_tests.
    task_progress_module._set_revocation_store_for_tests(
        InMemoryTaskRevocationStore()
    )
    yield
    task_progress_module._reset_revocation_store_for_tests()


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


def test_finalize_from_celery_success_targets_explicit_logical_run_id():
    calls = []

    class _AtomicStore(InMemoryTaskResultStore):
        def put_terminal_with_run(self, task_id, **kwargs):
            calls.append((task_id, kwargs))

    original = {"success": True, "rows_written": 3}
    finalize_from_celery(
        "task-1",
        "SUCCESS",
        original,
        None,
        name="carica.execute",
        run_id="logical-run-1",
        store=_AtomicStore(),
    )

    assert calls[0][1]["payload"]["run_id"] == "logical-run-1"
    assert original == {"success": True, "rows_written": 3}


def test_finalize_from_celery_maps_application_failure_to_failure_state():
    store = InMemoryTaskResultStore()
    payload = {"success": False, "message": "pipeline non valida"}

    finalize_from_celery(
        "t1", "SUCCESS", payload, None,
        name="pipeline.execute", store=store,
    )

    record = store.get("t1")
    assert record is not None
    assert record.state == FAILURE_STATE
    assert record.payload == payload


def test_finalize_uses_atomic_task_run_boundary_when_available():
    calls = []

    class _AtomicStore(InMemoryTaskResultStore):
        def put_terminal_with_run(self, task_id, **kwargs):
            calls.append((task_id, kwargs))

    payload = {"success": True, "run_id": "run-1", "project_id": "p1"}
    finalize_task_result(
        "task-1",
        state=SUCCESS_STATE,
        payload=payload,
        name="pipeline.execute",
        store=_AtomicStore(),
    )

    assert calls == [(
        "task-1",
        {
            "state": SUCCESS_STATE,
            "payload": payload,
            "name": "pipeline.execute",
        },
    )]


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


def test_finalize_from_celery_exposes_safe_structured_memory_failure():
    from app.core.resources.exceptions import MemoryLimitExceededError

    store = InMemoryTaskResultStore()

    class _Einfo:
        exception = MemoryLimitExceededError(
            observed_bytes=1_600,
            limit_bytes=1_500,
            path_basename="join_output",
            stage="data_plane_child_rss",
        )

    finalize_from_celery(
        "t-memory",
        "FAILURE",
        None,
        _Einfo(),
        name="pipeline.execute",
        store=store,
    )

    record = store.get("t-memory")
    assert record is not None
    assert record.payload == {
        "success": False,
        "error": "MemoryLimitExceededError",
        "error_code": "memory_limit_exceeded",
        "message": (
            "Memory limit exceeded in stage 'data_plane_child_rss' for "
            "'join_output': observed=1600 bytes, limit=1500 bytes."
        ),
        "observed_bytes": 1_600,
        "limit_bytes": 1_500,
        "path_basename": "join_output",
        "stage": "data_plane_child_rss",
        "run_id": "t-memory",
    }


@pytest.mark.parametrize("status", ["RETRY", "REVOKED", "STARTED", "PENDING"])
def test_finalize_from_celery_skips_non_terminal_states(status):
    store = InMemoryTaskResultStore()
    finalize_from_celery("t1", status, {"x": 1}, None, store=store)
    # Stati non terminali (senza eccezione): nessuna scrittura.
    assert store.get("t1") is None


# Annullamento cooperativo (Plenora 2.0, Fase 5 F5.2)


def test_raise_if_task_revoked_raises_when_flag_set():
    revocations = InMemoryTaskRevocationStore()
    revocations.revoke("t1")

    with pytest.raises(TaskRevoked):
        raise_if_task_revoked("t1", store=revocations)


def test_raise_if_task_revoked_noop_when_not_revoked():
    raise_if_task_revoked("t1", store=InMemoryTaskRevocationStore())
    raise_if_task_revoked(None, store=InMemoryTaskRevocationStore())


def test_task_revoked_is_base_exception_not_exception():
    # Deve attraversare i `except Exception` dei corpi task: NON e' Exception.
    assert issubclass(TaskRevoked, BaseException)
    assert not issubclass(TaskRevoked, Exception)


def test_report_progress_raises_taskrevoked_and_skips_write_when_revoked():
    revocations = InMemoryTaskRevocationStore()
    revocations.revoke("t1")
    task_progress_module._set_revocation_store_for_tests(revocations)
    result_store = InMemoryTaskResultStore()

    with pytest.raises(TaskRevoked):
        report_task_progress("t1", 50, "in corso", store=result_store)

    # Il checkpoint aborta PRIMA di scrivere il progress.
    assert result_store.get("t1") is None


def test_report_progress_writes_when_not_revoked():
    task_progress_module._set_revocation_store_for_tests(
        InMemoryTaskRevocationStore()
    )
    store = InMemoryTaskResultStore()
    report_task_progress("t1", 10, "ok", store=store)
    assert store.get("t1") is not None


def test_finalize_revoked_writes_revoked_state():
    store = InMemoryTaskResultStore()
    finalize_revoked("t1", name="carica.execute", store=store)

    record = store.get("t1")
    assert record is not None
    assert record.state == REVOKED_STATE
    assert record.payload == {
        "success": False,
        "status": "revoked",
        "message": "Task annullato",
    }


def test_finalize_from_celery_maps_taskrevoked_to_revoked():
    store = InMemoryTaskResultStore()

    class _Einfo:
        exception = TaskRevoked("t1")

    # Anche se il middleware passa status="FAILURE", una TaskRevoked mappa REVOKED.
    finalize_from_celery("t1", "FAILURE", None, _Einfo(), store=store)

    record = store.get("t1")
    assert record is not None
    assert record.state == REVOKED_STATE


def test_finalize_revoked_targets_explicit_logical_run_id():
    calls = []

    class _AtomicStore(InMemoryTaskResultStore):
        def put_terminal_with_run(self, task_id, **kwargs):
            calls.append((task_id, kwargs))

    finalize_revoked(
        "task-1",
        name="tasks.execute_enrichment",
        run_id="logical-run-1",
        store=_AtomicStore(),
    )

    assert calls[0][1]["payload"]["run_id"] == "logical-run-1"
