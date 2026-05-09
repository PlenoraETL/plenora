"""
Tests the tasks router behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract, pytest.mark.regression]


def test_task_service_compat_wrapper_preserves_canonical_singleton():
    from app.core.orchestrator.task_service import task_service as canonical_task_service
    from app.core.orchestrator.task_service import task_service as compat_task_service

    assert compat_task_service is canonical_task_service


def test_cleanup_task_deletes_only_task_result_and_task_logs(client, tmp_path):
    project_id = "proj-1"
    task_id = "task-1"
    results_dir = tmp_path / project_id
    results_dir.mkdir(parents=True, exist_ok=True)
    task_file = results_dir / "task-output.csv"
    task_file.write_text("id\n1\n", encoding="utf-8")

    task_info = SimpleNamespace(
        project_id=project_id,
        result={"project_id": project_id, "result_id": "res-1", "output_file": "task-output.csv"},
    )
    deleted_sql = []

    def _record_sql(sql, params):
        deleted_sql.append((sql, params))
        return SimpleNamespace(rowcount=1)

    with patch("app.core.orchestrator.router.settings", new=SimpleNamespace(RESULTS_DIR=tmp_path)), \
         patch("app.core.orchestrator.task_service.task_service.get_task_status", return_value=task_info), \
         patch("app.core.projects.service.ProjectService.get_project_metadata", return_value={"id": project_id, "group_id": "grp-1"}), \
         patch("app.core.results.cleanup.delete_result", return_value=True) as delete_result, \
         patch("app.db.pg_connection.execute", side_effect=_record_sql), \
         patch("celery.result.AsyncResult", return_value=SimpleNamespace(forget=lambda: None)):
        response = client.delete(f"/api/tasks/{task_id}/full-cleanup")

    assert response.status_code == 204
    delete_result.assert_called_once_with("res-1")
    assert not task_file.exists()
    assert deleted_sql == [
        ("DELETE FROM log_runs WHERE run_id = %s", (task_id,)),
    ]


def test_download_task_result_returns_gone_when_payload_has_no_result_id(client):
    task_info = SimpleNamespace(
        status=SimpleNamespace(value="success"),
        result={"project_id": "proj-1"},
    )

    with patch("app.core.orchestrator.router._assert_task_group"), \
         patch("app.core.orchestrator.task_service.task_service.get_task_status", return_value=task_info):
        response = client.get("/api/tasks/task-1/download")

    assert response.status_code == 410
    assert "non e' piu' disponibile" in response.json()["detail"]
