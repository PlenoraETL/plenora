"""Integration async-lite: backend + redis + worker + task status API."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.projects import managed_project


def test_unknown_task_returns_404(integration_backend_server, integration_auth_headers):
    status, body, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/tasks/not-a-real-task-id",
        headers=integration_auth_headers,
        timeout=10.0,
    )
    assert status == 404
    detail = body["detail"]
    if isinstance(detail, dict):
        assert detail["error"] == "task_not_found"
        assert "Task" in detail["message"]
    else:
        assert "Task" in detail


def test_task_status_progresses_with_real_worker(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
    integration_db_probe,
):
    _ = integration_celery_worker
    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Preview Project",
        project_type="estrai",
        canvas={"nodes": [], "edges": []},
    ) as created_project:
        project_id = created_project["metadata"]["id"]
        status, created, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}/preview?limit=5",
            headers=integration_auth_headers,
            timeout=10.0,
        )
        assert status == 200
        assert created["status"] in ("pending", "started", "progress")
        task_id = created["task_id"]

        payload = poll_json_until(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
            headers=integration_auth_headers,
            timeout=30.0,
            interval=1.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert payload["task_id"] == task_id
        assert payload["status"] in ("success", "failure")


def test_execute_endpoint_creates_real_task_and_reaches_terminal_state(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
    integration_db_probe,
):
    _ = integration_celery_worker
    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Execute Project",
        project_type="estrai",
        canvas={"nodes": [], "edges": []},
    ) as created_project:
        project_id = created_project["metadata"]["id"]
        status, created, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}/execute",
            method="POST",
            payload={"force_recalculate": False},
            headers=integration_auth_headers,
            timeout=10.0,
        )
        assert status == 200
        assert created["status"] in ("pending", "started", "progress")
        task_id = created["task_id"]

        payload = poll_json_until(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
            headers=integration_auth_headers,
            timeout=30.0,
            interval=1.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert payload["task_id"] == task_id
        assert payload["project_id"] == project_id
        assert payload["status"] in ("success", "failure")
