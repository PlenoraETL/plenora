"""Functional async-lite: backend + worker + task polling."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until


def test_preview_task_reaches_terminal_state_with_real_worker(
    backend_server,
    celery_worker,
    auth_headers,
):
    _ = celery_worker
    create_payload = {
        "name": "Functional Async Preview",
        "description": "tests2 functional",
        "project_type": "estrai",
        "default_connection_id": None,
        "canvas": {"nodes": [], "edges": []},
    }

    status, created_project, _ = http_json_response(
        f"{backend_server['base_url']}/api/projects",
        method="POST",
        payload=create_payload,
        headers=auth_headers,
        timeout=10.0,
    )
    # POST /api/projects risponde 201 Created (router status_code=201)
    assert status == 201
    project_id = created_project["metadata"]["id"]

    try:
        status, task_created, _ = http_json_response(
            f"{backend_server['base_url']}/api/projects/{project_id}/preview?limit=5",
            headers=auth_headers,
            timeout=10.0,
        )
        assert status == 200
        assert task_created["status"] in ("pending", "started", "progress")
        task_id = task_created["task_id"]

        final_status = poll_json_until(
            f"{backend_server['base_url']}/api/tasks/{task_id}",
            headers=auth_headers,
            timeout=30.0,
            interval=1.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["task_id"] == task_id
        assert final_status["project_id"] == project_id
        assert final_status["status"] in ("success", "failure")
    finally:
        status, _, _ = http_json_response(
            f"{backend_server['base_url']}/api/projects/{project_id}",
            method="DELETE",
            headers=auth_headers,
            timeout=10.0,
        )
        # DELETE /api/projects/{id} risponde 204 No Content (F2-B2 2026-04-24)
        assert status in (200, 204, 404)
