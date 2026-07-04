"""Integration: project_source senza latest artifact deve triggerare il recompute upstream."""

from __future__ import annotations

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
                "fileName": "project_source_missing_latest",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }


def test_project_source_without_latest_artifact_triggers_upstream_recompute(
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
                name="Integration Upstream Without Execute",
                project_type="estrai",
                canvas=_estrai_mysql_canvas(seeded["table_name"]),
                default_connection_id=connection["id"],
            ) as upstream:
                upstream_project_id = upstream["metadata"]["id"]

                with managed_project(
                    base_url=integration_backend_server["base_url"],
                    auth_headers=integration_auth_headers,
                    name="Integration Downstream Missing Latest",
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
                    assert downstream_status["result"]["rows_count"] == 2
                    assert downstream_status["result"]["columns"] == ["id", "city", "score"]
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
