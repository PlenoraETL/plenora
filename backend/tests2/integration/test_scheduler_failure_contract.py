"""Integration SCHEDULER: run manuale su progetto che fallisce -> runtime/log coerenti."""

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
                "fileName": "scheduler_failure_output",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }


def test_scheduler_manual_run_failure_updates_runtime_and_logs(
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
            name="Integration Scheduler Failure Project",
            project_type="estrai",
            canvas=_estrai_missing_mysql_canvas("it_missing_table_scheduler"),
            default_connection_id=connection["id"],
        ) as created:
            project_id = created["metadata"]["id"]

            status, schedule_created, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/schedules",
                method="POST",
                payload={
                    "project_id": project_id,
                    "cron_expression": "0 3 * * *",
                    "timezone": "Europe/Rome",
                    "description": "tests2 scheduler failure",
                    "use_cache": False,
                    "max_retries": 0,
                    "retry_backoff_seconds": [],
                    "append_conflicts": "skip",
                    "log_skipped_rows": True,
                    "concurrency_max_queue": 1,
                },
                headers=integration_auth_headers,
                timeout=10.0,
            )
            assert status in (200, 201)
            schedule_id = schedule_created["id"]

            try:
                status, run_started, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}/run",
                    method="POST",
                    headers=integration_auth_headers,
                    timeout=10.0,
                )
                assert status == 200
                task_id = run_started["task_id"]

                final_status = poll_json_until(
                    f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
                    headers=integration_auth_headers,
                    timeout=120.0,
                    interval=2.0,
                    predicate=lambda body: isinstance(body, dict)
                    and body.get("status") in ("success", "failure"),
                )
                assert final_status["status"] == "failure"

                schedule_body = poll_json_until(
                    f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}",
                    headers=integration_auth_headers,
                    timeout=60.0,
                    interval=2.0,
                    predicate=lambda body: isinstance(body, dict)
                    and body.get("runtime", {}).get("status") == "failed",
                )
                assert schedule_body["runtime"]["status"] == "failed"
                assert schedule_body["runtime"]["failure_count"] >= 1
                assert schedule_body["runtime"]["last_execution"]["status"] == "failed"

                status, logs_body, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}/logs",
                    headers=integration_auth_headers,
                    timeout=10.0,
                )
                assert status == 200
                assert logs_body["total"] >= 1
                assert any(
                    log.get("level") == "ERROR"
                    and log.get("event") in ("PIPELINE_FAILED", "RETRY_EXHAUSTED")
                    for log in logs_body["logs"]
                )

                status, runs_body, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}/runs",
                    headers=integration_auth_headers,
                    timeout=10.0,
                )
                assert status == 200
                assert runs_body["runs_count"] == 0
            finally:
                status, _, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}",
                    method="DELETE",
                    headers=integration_auth_headers,
                    timeout=10.0,
                )
                assert status in (200, 204, 404)
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
