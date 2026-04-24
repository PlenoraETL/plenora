"""Functional: upload file reale + canvas ARRICCHISCI minimo + execute async."""

from __future__ import annotations

from socket import timeout as SocketTimeout
from urllib.request import Request, urlopen

from tests2.functional.harness.http import (
    http_json_response,
    http_multipart_response,
    poll_json_until,
)


def _arricchisci_file_canvas(file_source_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "fs-1",
                "type": "file_source",
                "position": {"x": 100, "y": 100},
                "file_source_id": file_source_id,
                "columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 350, "y": 100},
                "fileName": "arricchisci_functional",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "fs-1",
                "target": "result-1",
                "join_type": "output",
            }
        ],
    }


def test_arricchisci_execute_from_uploaded_file_reaches_success(
    backend_server,
    celery_worker,
    auth_headers,
):
    _ = celery_worker
    status, uploaded, _ = http_multipart_response(
        f"{backend_server['base_url']}/api/files/upload",
        data={
            "name": "Functional CSV Source",
            "description": "tests2 functional arricchisci",
        },
        files={
            "file": ("functional.csv", b"city,country\nRome,IT\n", "text/csv"),
        },
        headers=auth_headers,
        timeout=10.0,
    )
    # F3-B3 (2026-04-24): POST /api/files/upload ora risponde 201 Created.
    assert status == 201
    file_source_id = uploaded["id"]

    create_payload = {
        "name": "Functional Arricchisci Upload",
        "description": "tests2 functional",
        "project_type": "arricchisci",
        "default_connection_id": None,
        "canvas": _arricchisci_file_canvas(file_source_id),
    }
    status, created_project, _ = http_json_response(
        f"{backend_server['base_url']}/api/projects",
        method="POST",
        payload=create_payload,
        headers=auth_headers,
        timeout=10.0,
    )
    # F3-B2 (2026-04-24): POST /api/projects ora risponde 201 Created.
    assert status == 201
    project_id = created_project["metadata"]["id"]

    try:
        status, task_created, _ = http_json_response(
            f"{backend_server['base_url']}/api/projects/{project_id}/execute",
            method="POST",
            payload={"force_recalculate": False},
            headers=auth_headers,
            timeout=10.0,
        )
        assert status == 200
        task_id = task_created["task_id"]

        final_status = poll_json_until(
            f"{backend_server['base_url']}/api/tasks/{task_id}",
            headers=auth_headers,
            timeout=120.0,
            interval=2.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["status"] == "success"
        assert final_status["project_id"] == project_id
        assert final_status["result"]["success"] is True
        assert final_status["result"]["result_id"]

        req = Request(
            f"{backend_server['base_url']}/api/tasks/{task_id}/download",
            headers=auth_headers,
            method="GET",
        )
        with urlopen(req, timeout=10.0) as resp:
            payload = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "city" in payload
            assert "Rome" in payload
    finally:
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/projects/{project_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            assert status in (200, 404)
        except (TimeoutError, SocketTimeout):
            pass
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/files/{file_source_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            assert status in (200, 404)
        except (TimeoutError, SocketTimeout):
            pass
