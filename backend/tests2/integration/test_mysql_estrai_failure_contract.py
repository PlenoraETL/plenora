"""Integration failure-path: ESTRAI con tabella mancante deve fallire esplicitamente."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_mysql_connection_and_estrai_preview import (
    _create_mysql_connection,
    _delete_connection,
)


def _estrai_missing_mysql_canvas(table_name: str) -> dict:
    return {
        "nodes": [
            {
                "id": "src-1",
                "type": "source",
                "position": {"x": 100, "y": 100},
                "table_name": table_name,
                "alias": "src",
                "columns": [
                    {"name": "id", "type": "int", "visible": True},
                    {"name": "city", "type": "str", "visible": True},
                ],
                "filters": [],
                "calculated_columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 350, "y": 100},
                "fileName": "mysql_missing_table",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }


def test_mysql_execute_with_missing_table_returns_failure_status(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
):
    _ = integration_celery_worker
    connection = _create_mysql_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    try:
        with managed_project(
            base_url=integration_backend_server["base_url"],
            auth_headers=integration_auth_headers,
            name="Integration MySQL Missing Table",
            project_type="estrai",
            canvas=_estrai_missing_mysql_canvas("it_missing_table_backend"),
            default_connection_id=connection["id"],
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
                timeout=90.0,
                interval=2.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert final_status["status"] == "failure"
            error = (final_status.get("error") or "") + " " + str(final_status.get("body", ""))
            result_payload = str(final_status.get("result", "")) + " " + str(final_status)
            combined = f"{error} {result_payload}".lower()
            assert "missing" in combined or "doesn't exist" in combined or "does not exist" in combined or "not found" in combined
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
