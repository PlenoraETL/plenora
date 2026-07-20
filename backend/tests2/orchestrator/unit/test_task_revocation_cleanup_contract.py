"""Regressioni sul cleanup cooperativo dei task annullati."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.orchestrator.task_progress import TaskRevoked


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


def _raise_on_second_progress():
    calls = 0

    def _progress(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TaskRevoked("task-1")

    return _progress


def test_cancel_executable_run_persists_cancelled_status(monkeypatch):
    from app.core.eseguibili import db as eseguibili_db

    statements = []
    monkeypatch.setattr(
        eseguibili_db,
        "_execute",
        lambda sql, params=(): statements.append((sql, params)),
    )

    eseguibili_db.cancel_eseguibile_run("run-1")

    assert "status = 'cancelled'" in statements[0][0]
    assert statements[0][1] == ("Task annullato", "run-1")


def test_pipeline_revocation_closes_run_and_worker_connection(monkeypatch):
    from workers.tasks import pipeline_task as task

    log_closes = []
    intermediate_closes = []
    connection_closes = []

    monkeypatch.setattr(
        task.run_idempotency_module, "is_already_completed", lambda _run_id: False
    )
    monkeypatch.setattr(
        task.async_user_recheck_module,
        "recheck_user_authorized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        task,
        "_get_project_info",
        lambda _project_id: {
            "group_id": "group-1",
            "project_type": "manipola",
            "retention_n": 5,
        },
    )
    monkeypatch.setattr(task, "open_run", lambda **_kwargs: None)
    monkeypatch.setattr(task, "close_run", lambda **kwargs: log_closes.append(kwargs))
    monkeypatch.setattr(task, "_update_progress", _raise_on_second_progress())
    monkeypatch.setattr(
        task.run_lifecycle_module, "create_run", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        task.run_lifecycle_module,
        "close_run",
        lambda **kwargs: intermediate_closes.append(kwargs),
    )
    monkeypatch.setattr(
        task.run_lifecycle_module,
        "close_worker_connection",
        lambda: connection_closes.append(True),
    )

    shim = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        name="pipeline.execute",
    )
    with pytest.raises(TaskRevoked):
        task.execute_pipeline_task(
            shim,
            project_id="project-1",
            run_id="logical-run-1",
        )

    assert log_closes[-1]["status"] == "revoked"
    assert log_closes[-1]["run_id"] == "logical-run-1"
    assert intermediate_closes[-1]["success"] is False
    assert connection_closes == [True]


def test_pipeline_setup_failure_closes_log_run_and_worker_connection(monkeypatch):
    from workers.tasks import pipeline_task as task

    log_closes = []
    connection_closes = []
    monkeypatch.setattr(
        task.run_idempotency_module, "is_already_completed", lambda _run_id: False
    )
    monkeypatch.setattr(
        task.async_user_recheck_module,
        "recheck_user_authorized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        task,
        "_get_project_info",
        lambda _project_id: {
            "group_id": "group-1",
            "project_type": "manipola",
            "retention_n": 5,
        },
    )
    monkeypatch.setattr(task, "open_run", lambda **_kwargs: None)
    monkeypatch.setattr(task, "set_run_context", lambda **_kwargs: None)
    monkeypatch.setattr(task, "close_run", lambda **kwargs: log_closes.append(kwargs))
    monkeypatch.setattr(task, "_update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task.run_lifecycle_module,
        "create_run",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("run store down")),
    )
    monkeypatch.setattr(
        task.run_lifecycle_module,
        "close_worker_connection",
        lambda: connection_closes.append(True),
    )

    shim = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        name="pipeline.execute",
    )
    with pytest.raises(RuntimeError, match="run store down"):
        task.execute_pipeline_task(
            shim,
            project_id="project-1",
            run_id="logical-run-1",
        )

    assert log_closes[-1]["status"] == "failure"
    assert connection_closes == [True]


def test_pipeline_revocation_before_run_closes_executable_and_temp_inputs(monkeypatch):
    from app.core.eseguibili import service as eseguibili_service
    from app.core.files.service import file_source_service
    from workers.tasks import pipeline_task as task

    executable_closes = []
    input_deletes = []
    connection_closes = []
    monkeypatch.setattr(
        task.run_idempotency_module, "is_already_completed", lambda _run_id: False
    )
    monkeypatch.setattr(
        task.async_user_recheck_module,
        "recheck_eseguibile_authorized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        task,
        "_update_progress",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskRevoked("task-1")),
    )
    monkeypatch.setattr(
        eseguibili_service,
        "cancel_eseguibile_run",
        lambda run_id, **kwargs: executable_closes.append((run_id, kwargs)),
    )
    monkeypatch.setattr(
        file_source_service,
        "delete_file_source",
        lambda file_source_id: input_deletes.append(file_source_id),
    )
    monkeypatch.setattr(
        task.run_lifecycle_module,
        "close_worker_connection",
        lambda: connection_closes.append(True),
    )

    contract = {
        "schema_version": 2,
        "project_id": "executable-1",
        "project_type": "manipola",
        "group_id": "group-1",
        "canvas": {"nodes": [], "edges": []},
        "execution_subject": {
            "kind": "eseguibile",
            "id": "executable-1",
            "run_id": "executable-run-1",
        },
        "runtime_context": {"cleanup_file_source_ids": ["temp-file-1"]},
    }
    shim = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        name="pipeline.execute",
    )

    with pytest.raises(TaskRevoked):
        task.execute_pipeline_task(
            shim,
            project_id="executable-1",
            run_id="logical-run-1",
            execution_contract=contract,
        )

    assert input_deletes == ["temp-file-1"]
    assert executable_closes == [
        (
            "executable-run-1",
            {"error_message": "Task annullato"},
        )
    ]
    assert connection_closes == [True]


def test_enrichment_revocation_closes_run_and_removes_temp_inputs(monkeypatch):
    from tests2._helpers.enrichment_task_fixtures import make_minimal_source_canvas
    from tests2._helpers.enrichment_task_harness import (
        install_enrichment_task_mocks,
        invoke_enrichment_task,
    )
    from workers.tasks import enrichment_task as task

    spies = install_enrichment_task_mocks(monkeypatch)
    cleanup_calls = []
    connection_closes = []
    monkeypatch.setattr(task, "update_task_progress", _raise_on_second_progress())
    monkeypatch.setattr(
        task,
        "_cleanup_temp_file_sources",
        lambda ids, task_id: cleanup_calls.append((ids, task_id)),
    )
    monkeypatch.setattr(
        "app.core.intermediate.cleanup.close_worker_connection",
        lambda: connection_closes.append(True),
    )

    with pytest.raises(TaskRevoked):
        invoke_enrichment_task(
            project_id="project-1",
            canvas=make_minimal_source_canvas(),
            run_id="logical-run-1",
        )

    assert spies.intermediate_close_runs[-1]["success"] is False
    assert spies.logger_close_runs[-1]["status"] == "revoked"
    assert cleanup_calls[-1][1] == "logical-run-1"
    assert connection_closes == [True]


def test_enrichment_revocation_before_run_releases_worker_connection(monkeypatch):
    from tests2._helpers.enrichment_task_harness import (
        install_enrichment_task_mocks,
    )
    from workers.tasks import enrichment_task as task

    install_enrichment_task_mocks(monkeypatch)
    cleanup_calls = []
    connection_closes = []
    monkeypatch.setattr(
        task,
        "update_task_progress",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskRevoked("task-1")),
    )
    monkeypatch.setattr(
        task,
        "_cleanup_temp_file_sources",
        lambda ids, task_id: cleanup_calls.append((ids, task_id)),
    )
    monkeypatch.setattr(
        "app.core.intermediate.cleanup.close_worker_connection",
        lambda: connection_closes.append(True),
    )
    shim = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        name="tasks.execute_enrichment",
    )

    with pytest.raises(TaskRevoked):
        task.execute_enrichment_task(
            shim,
            project_id="project-1",
            canvas={"nodes": [], "edges": []},
            cleanup_file_source_ids=["temp-file-1"],
            run_id="logical-run-1",
        )

    assert cleanup_calls == [(["temp-file-1"], "task-1")]
    assert connection_closes == [True]


def test_carica_revocation_closes_run_and_worker_connections(monkeypatch):
    from workers.tasks import carica_task as task

    log_closes = []
    connection_closes = []

    class _ProjectService:
        def get_project_metadata(self, _project_id):
            return SimpleNamespace(group_id="group-1")

    monkeypatch.setattr(task, "is_already_completed", lambda _run_id: False)
    monkeypatch.setattr(task, "recheck_user_authorized", lambda **_kwargs: None)
    monkeypatch.setattr(task, "ProjectService", _ProjectService)
    monkeypatch.setattr(task, "open_run", lambda **_kwargs: None)
    monkeypatch.setattr(task, "close_run", lambda **kwargs: log_closes.append(kwargs))
    monkeypatch.setattr(
        task,
        "_update_progress",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TaskRevoked("task-1")),
    )
    monkeypatch.setattr(
        task,
        "close_worker_connection",
        lambda: connection_closes.append("intermediate"),
    )
    monkeypatch.setattr(
        task,
        "_close_config_connection",
        lambda: connection_closes.append("config"),
    )

    shim = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        name="carica.execute",
    )
    with pytest.raises(TaskRevoked):
        task.execute_carica_task(
            shim,
            project_id="project-1",
            canvas={"nodes": [], "edges": []},
            carica_node_data={},
            run_id="logical-run-1",
        )

    assert log_closes[-1]["status"] == "revoked"
    assert log_closes[-1]["run_id"] == "logical-run-1"
    assert connection_closes == ["intermediate", "config"]


def test_carica_permission_revoke_releases_worker_connections(monkeypatch):
    from workers.tasks import carica_task as task

    connection_closes = []
    monkeypatch.setattr(task, "is_already_completed", lambda _run_id: False)
    monkeypatch.setattr(
        task,
        "recheck_user_authorized",
        lambda **_kwargs: {
            "status": "permission_revoked",
            "success": False,
            "message": "Permesso revocato",
        },
    )
    monkeypatch.setattr(
        task,
        "close_worker_connection",
        lambda: connection_closes.append("intermediate"),
    )
    monkeypatch.setattr(
        task,
        "_close_config_connection",
        lambda: connection_closes.append("config"),
    )
    shim = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        name="carica.execute",
    )

    result = task.execute_carica_task(
        shim,
        project_id="project-1",
        canvas={"nodes": [], "edges": []},
        carica_node_data={},
        run_id="logical-run-1",
        user_id="user-1",
    )

    assert result["status"] == "permission_revoked"
    assert connection_closes == ["intermediate", "config"]
