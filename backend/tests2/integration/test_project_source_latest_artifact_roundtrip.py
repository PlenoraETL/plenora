"""Integration: project_source usa davvero il latest artifact persistito."""

from __future__ import annotations

from urllib.request import Request, urlopen

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.projects import managed_project
from tests2.integration.harness.mysql import seeded_mysql_table
from tests2.integration.test_mysql_connection_and_estrai_preview import (
    _create_mysql_connection,
    _delete_connection,
    _estrai_mysql_canvas,
)


def _arricchisci_project_source_canvas(source_project_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "src-1",
                "type": "project_source",
                "position": {"x": 100, "y": 100},
                "source_project_id": source_project_id,
                "columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 350, "y": 100},
                "fileName": "project_source_latest_artifact",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }


def test_project_source_resolves_latest_blob_backed_artifact(
    integration_task_worker,
    integration_backend_server,
    integration_auth_headers,
):
    _ = integration_task_worker
    connection = _create_mysql_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    try:
        with seeded_mysql_table() as seeded:
            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration Upstream ESTRAI",
                project_type="estrai",
                canvas=_estrai_mysql_canvas(seeded["table_name"]),
                default_connection_id=connection["id"],
            ) as upstream:
                upstream_project_id = upstream["metadata"]["id"]

                status, upstream_task, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/projects/{upstream_project_id}/execute",
                    method="POST",
                    payload={"force_recalculate": False},
                    headers=integration_auth_headers,
                    timeout=10.0,
                )
                assert status == 200
                upstream_task_id = upstream_task["task_id"]

                upstream_status = poll_json_until(
                    f"{integration_backend_server['base_url']}/api/tasks/{upstream_task_id}",
                    headers=integration_auth_headers,
                    timeout=120.0,
                    interval=2.0,
                    predicate=lambda body: isinstance(body, dict)
                    and body.get("status") in ("success", "failure"),
                )
                assert upstream_status["status"] == "success"
                assert upstream_status["result"]["success"] is True
                assert upstream_status["result"]["result_id"]

                with managed_project(
                    base_url=integration_backend_server["base_url"],
                    auth_headers=integration_auth_headers,
                    name="Integration Downstream project_source",
                    project_type="arricchisci",
                    canvas=_arricchisci_project_source_canvas(upstream_project_id),
                ) as downstream:
                    downstream_project_id = downstream["metadata"]["id"]

                    status, downstream_task, _ = http_json_response(
                        f"{integration_backend_server['base_url']}/api/projects/{downstream_project_id}/execute",
                        method="POST",
                        payload={"force_recalculate": False},
                        headers=integration_auth_headers,
                        timeout=10.0,
                    )
                    assert status == 200
                    downstream_task_id = downstream_task["task_id"]

                    downstream_status = poll_json_until(
                        f"{integration_backend_server['base_url']}/api/tasks/{downstream_task_id}",
                        headers=integration_auth_headers,
                        timeout=120.0,
                        interval=2.0,
                        predicate=lambda body: isinstance(body, dict)
                        and body.get("status") in ("success", "failure"),
                    )
                    assert downstream_status["status"] == "success"
                    assert downstream_status["result"]["success"] is True
                    assert downstream_status["result"]["result_id"]

                    req = Request(
                        f"{integration_backend_server['base_url']}/api/projects/{downstream_project_id}/download",
                        headers=integration_auth_headers,
                        method="GET",
                    )
                    with urlopen(req, timeout=10.0) as resp:
                        payload = resp.read().decode("utf-8")
                        assert resp.status == 200
                        assert "Rome" in payload
                        assert "Milan" in payload
                        assert "score" in payload
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
