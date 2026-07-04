"""Integration: ARRICCHISCI reale con canvas minimo file_source -> result."""

from __future__ import annotations

from urllib.request import Request, urlopen

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.projects import managed_project


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
                "fileName": "arricchisci_integration",
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


def test_arricchisci_execute_from_seeded_file_source_returns_downloadable_result(
    integration_task_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_task_worker
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[{"city": "Rome", "country": "IT"}],
    )
    canvas = _arricchisci_file_canvas(file_source["id"])

    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Arricchisci File",
        project_type="arricchisci",
        canvas=canvas,
    ) as created:
        project_id = created["metadata"]["id"]

        status, task_created, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}/execute",
            method="POST",
            payload={"force_recalculate": False},
            headers=integration_auth_headers,
            timeout=10.0,
        )
        assert status == 200
        task_id = task_created["task_id"]

        final_status = poll_json_until(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
            headers=integration_auth_headers,
            timeout=60.0,
            interval=1.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["status"] == "success"
        assert final_status["project_id"] == project_id
        assert final_status["result"]["success"] is True
        assert final_status["result"]["result_id"]

        req = Request(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}/download",
            headers=integration_auth_headers,
            method="GET",
        )
        with urlopen(req, timeout=10.0) as resp:
            payload = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "city" in payload
            assert "Rome" in payload
