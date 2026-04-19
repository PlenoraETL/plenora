"""Integration: task status + download su esecuzione reale."""

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
                "fileName": "task_download_contract",
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


def test_download_task_result_reads_blob_from_real_task_execution(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_celery_worker
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[{"city": "Rome", "country": "IT"}],
    )

    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="Integration Download Project",
        project_type="arricchisci",
        canvas=_arricchisci_file_canvas(file_source["id"]),
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
        task_id = created["task_id"]

        body = poll_json_until(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
            headers=integration_auth_headers,
            timeout=120.0,
            interval=1.0,
            request_timeout=10.0,
            predicate=lambda payload: isinstance(payload, dict)
            and payload.get("status") in ("success", "failure"),
        )
        assert body["status"] == "success"
        assert body["project_id"] == project_id
        assert body["result"]["result_id"]

        req = Request(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}/download",
            headers=integration_auth_headers,
            method="GET",
        )
        with urlopen(req, timeout=10.0) as resp:
            payload = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "attachment;" in resp.headers["Content-Disposition"]
            assert "city" in payload
            assert "Rome" in payload
