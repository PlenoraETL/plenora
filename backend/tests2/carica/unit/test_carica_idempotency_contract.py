"""
Idempotency guard per carica_task (C4 round 2, opzione B).

Contract: se carica_task viene invocato con un `run_id` esplicito che
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


# Helper puro: is_already_completed


def test_is_already_completed_returns_false_when_run_does_not_exist():
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch("app.core.intermediate.runs_db.get_run", return_value=None):
        assert is_already_completed("run-missing") is False


def test_is_already_completed_returns_true_for_completed_run():
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "run-1", "status": "completed"},
    ):
        assert is_already_completed("run-1") is True


def test_is_already_completed_returns_false_for_failed_run():
    """Status 'failed' deve consentire retry, NON essere treated come completed."""
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "run-1", "status": "failed"},
    ):
        assert is_already_completed("run-1") is False


def test_is_already_completed_returns_false_for_running_run():
    """Status 'running' e' una race rara — non blocchiamo un retry
    manuale dell'operatore su un task che sembra bloccato."""
    from app.core.intermediate.run_idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "run-1", "status": "running"},
    ):
        assert is_already_completed("run-1") is False


def test_is_already_completed_fails_closed_on_db_error():
    from app.core.intermediate.run_idempotency import (
        IdempotencyStateUnavailable,
        is_already_completed,
    )

    with patch(
        "app.core.intermediate.runs_db.get_run",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(IdempotencyStateUnavailable):
            is_already_completed("run-1")


# Integration nel task: accetta run_id kwarg + skip se completato


def test_carica_task_accepts_run_id_kwarg():
    """execute_carica_task deve accettare `run_id` optional."""
    from workers.tasks.carica_task import execute_carica_task

    sig = inspect.signature(execute_carica_task)
    assert "run_id" in sig.parameters, (
        "execute_carica_task non accetta run_id come kwarg per idempotency"
    )
    # Default None → generazione fresh (backward compat)
    assert sig.parameters["run_id"].default is None


def test_carica_task_references_idempotency_helper():
    """Il task deve referenziare `is_already_completed` come callee.

    Replacement R-04b di `inspect.getsource(...) + "is_already_completed" in source`:
    bytecode probe sul callee identifier (refactor-resilient).
    """
    from workers.tasks.carica_task import execute_carica_task

    code = getattr(execute_carica_task, "__wrapped__", execute_carica_task).__code__

    def _walk(co):
        if "is_already_completed" in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    assert _walk(code), (
        "execute_carica_task non riferisce is_already_completed. "
        "Retry manuale con stesso run_id eseguirebbe due volte."
    )


def test_successful_write_with_unpersisted_completion_is_uncertain(monkeypatch):
    from workers.tasks import carica_task

    monkeypatch.setattr(carica_task, "is_already_completed", lambda _run: False)
    monkeypatch.setattr(carica_task, "_update_progress", lambda *a, **kw: None)
    monkeypatch.setattr(carica_task, "open_run", lambda **kw: None)
    monkeypatch.setattr(carica_task, "close_run", lambda **kw: None)
    monkeypatch.setattr(carica_task, "set_run_context", lambda **kw: None)
    monkeypatch.setattr(
        carica_task.intermediate_run_lifecycle,
        "create_run",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        carica_task.intermediate_runs_db,
        "update_run",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("marker down")),
    )
    monkeypatch.setattr(
        "app.core.auth.async_user_recheck.recheck_user_authorized",
        lambda **kw: None,
    )
    target = SimpleNamespace(key="carica:db:test", kind="db")
    handle = SimpleNamespace(target=target, owner_token="owner-1")
    monkeypatch.setattr(
        carica_task,
        "require_carica_target_lock",
        lambda **kw: target,
    )
    monkeypatch.setattr(
        carica_task,
        "acquire_carica_target_lock",
        lambda **kw: handle,
    )
    monkeypatch.setattr(
        carica_task,
        "start_carica_target_lock_heartbeat",
        lambda *_a, **_kw: SimpleNamespace(
            stop=lambda: None,
            lost=False,
            error=None,
        ),
    )
    monkeypatch.setattr(
        carica_task,
        "release_carica_target_lock",
        lambda _handle: True,
    )
    monkeypatch.setattr(
        carica_task,
        "_load_upstream_data",
        lambda *a, **kw: pd.DataFrame({"id": [1, 2]}),
    )
    monkeypatch.setattr(
        carica_task.carica_dispatch,
        "execute_db_write",
        lambda *a, **kw: {
            "success": True,
            "rows_written": 2,
            "total_rows": 2,
        },
    )
    fake_self = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        update_state=MagicMock(),
    )
    node_data = {
        "connection_id": "conn-1",
        "schema_name": "public",
        "table_name": "dest",
        "write_mode": "append",
    }

    result = carica_task.execute_carica_task(
        fake_self,
        project_id="project-1",
        canvas={},
        carica_node_data=node_data,
        run_id="run-1",
        execution_contract={
            "schema_version": 1,
            "project_id": "project-1",
            "project_type": "manipola",
            "group_id": "group-1",
            "canvas": {},
            "carica_node_data": node_data,
        },
    )

    assert result["success"] is False
    assert result["status"] == "completion_uncertain"
    assert result["error"] == "carica_completion_uncertain"
    assert result["rows_written"] == 2


def test_external_write_failure_requires_operator_resolution(monkeypatch):
    """A remote error after dispatch must not authorize an unsafe replay."""
    from workers.tasks import carica_task

    monkeypatch.setattr(carica_task, "is_already_completed", lambda _run: False)
    monkeypatch.setattr(carica_task, "recheck_user_authorized", lambda **_kw: None)
    monkeypatch.setattr(carica_task, "_update_progress", lambda *a, **kw: None)
    monkeypatch.setattr(carica_task, "open_run", lambda **kw: None)
    monkeypatch.setattr(carica_task, "close_run", lambda **kw: None)
    monkeypatch.setattr(carica_task, "set_run_context", lambda **kw: None)
    monkeypatch.setattr(
        carica_task.intermediate_run_lifecycle,
        "create_run",
        lambda **kw: None,
    )
    update_run = MagicMock()
    monkeypatch.setattr(carica_task.intermediate_runs_db, "update_run", update_run)
    target = SimpleNamespace(key="carica:rest:test", kind="rest")
    handle = SimpleNamespace(target=target, owner_token="owner-1")
    monkeypatch.setattr(carica_task, "require_carica_target_lock", lambda **kw: target)
    monkeypatch.setattr(carica_task, "acquire_carica_target_lock", lambda **kw: handle)
    monkeypatch.setattr(
        carica_task,
        "start_carica_target_lock_heartbeat",
        lambda *_a, **_kw: SimpleNamespace(stop=lambda: None, lost=False, error=None),
    )
    monkeypatch.setattr(carica_task, "release_carica_target_lock", lambda _h: True)
    monkeypatch.setattr(
        carica_task,
        "_load_upstream_data",
        lambda *a, **kw: pd.DataFrame({"id": [1, 2]}),
    )
    monkeypatch.setattr(
        carica_task.carica_dispatch,
        "execute_rest_write",
        lambda *a, **kw: {
            "success": False,
            "status": "write_error",
            "error_message": "timeout",
            "rows_written": 0,
            "total_rows": 2,
        },
    )
    fake_self = SimpleNamespace(
        request=SimpleNamespace(id="task-rest-1"),
        update_state=MagicMock(),
    )
    node_data = {
        "dest_type": "rest",
        "rest_config": {"url": "https://example.test/write"},
    }

    result = carica_task.execute_carica_task(
        fake_self,
        project_id="project-1",
        canvas={},
        carica_node_data=node_data,
        run_id="run-rest-1",
        execution_contract={
            "schema_version": 1,
            "project_id": "project-1",
            "project_type": "manipola",
            "group_id": "group-1",
            "canvas": {},
            "carica_node_data": node_data,
        },
    )

    assert result["success"] is False
    assert result["status"] == "completion_uncertain"
    assert result["error"] == "carica_external_write_uncertain"
    assert result["run_id"] == "run-rest-1"
    update_run.assert_called_once_with(
        "run-rest-1", status="completion_uncertain",
    )
