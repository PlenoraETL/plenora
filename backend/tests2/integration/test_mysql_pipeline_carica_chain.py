"""Integration MySQL: chaining reale pipeline -> CARICA."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.mysql import mysql_execute, mysql_fetchall
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_mysql_carica_roundtrip import _arricchisci_carica_mysql_canvas
from tests2.integration.test_mysql_connection_and_estrai_preview import (
    _create_mysql_connection,
    _delete_connection,
)


def test_mysql_execute_pipeline_spawns_and_completes_carica_task(
    integration_task_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_task_worker
    connection = _create_mysql_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    table_name = f"it_pipe_carica_{integration_user_seed['group_id'][-8:]}"
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[
            {"city": "Rome", "country": "IT"},
            {"city": "Milan", "country": "IT"},
        ],
    )

    try:
        with managed_project(
            base_url=integration_backend_server["base_url"],
            auth_headers=integration_auth_headers,
            name="Integration MySQL Pipeline CARICA",
            project_type="arricchisci",
            canvas=_arricchisci_carica_mysql_canvas(file_source["id"], connection["id"], table_name),
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
            pipeline_task_id = task_created["task_id"]

            pipeline_status = poll_json_until(
                f"{integration_backend_server['base_url']}/api/tasks/{pipeline_task_id}",
                headers=integration_auth_headers,
                timeout=180.0,
                interval=2.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert pipeline_status["status"] == "success"
            assert pipeline_status["result"]["success"] is True
            assert "carica" in pipeline_status["result"]
            carica_tasks = pipeline_status["result"]["carica"]["tasks"]
            assert len(carica_tasks) == 1

            carica_task_id = carica_tasks[0]["task_id"]
            carica_status = poll_json_until(
                f"{integration_backend_server['base_url']}/api/tasks/{carica_task_id}",
                headers=integration_auth_headers,
                timeout=180.0,
                interval=2.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert carica_status["status"] == "success"
            assert carica_status["result"]["success"] is True
            assert carica_status["result"]["rows_written"] == 2

            rows = mysql_fetchall(f"SELECT city, country FROM `{table_name}` ORDER BY city")
            assert rows == [
                {"city": "Milan", "country": "IT"},
                {"city": "Rome", "country": "IT"},
            ]
    finally:
        try:
            mysql_execute(f"DROP TABLE IF EXISTS `{table_name}`")
        except Exception:
            pass
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
