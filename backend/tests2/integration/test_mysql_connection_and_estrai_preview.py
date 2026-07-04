"""Integration MySQL: connection reale + schema introspection + preview ESTRAI."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.canvas_builders import build_estrai_preview_canvas
from tests2.integration.harness.mysql import (
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
    require_mysql_enabled,
    seeded_mysql_table,
)
from tests2.integration.harness.projects import managed_project


def _create_mysql_connection(base_url: str, auth_headers: dict) -> dict:
    require_mysql_enabled()
    payload = {
        "name": "Integration MySQL Connection",
        "description": "tests2 mysql integration",
        "type": "mysql",
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": MYSQL_DATABASE,
        "db_schema": None,
        "username": MYSQL_USER,
        "password": MYSQL_PASSWORD,
    }
    status, created, _ = http_json_response(
        f"{base_url}/api/connections",
        method="POST",
        payload=payload,
        headers=auth_headers,
        timeout=10.0,
    )
    assert status in (200, 201)
    return created


def _delete_connection(base_url: str, auth_headers: dict, connection_id: str) -> None:
    status, _, _ = http_json_response(
        f"{base_url}/api/connections/{connection_id}",
        method="DELETE",
        headers=auth_headers,
        timeout=10.0,
    )
    assert status in (200, 204, 404)


def _estrai_mysql_canvas(table_name: str) -> dict:
    return build_estrai_preview_canvas(table_name, file_name="mysql_preview")


def test_mysql_connection_tables_and_estrai_preview_roundtrip(
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
            status, tested, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/connections/{connection['id']}/test",
                method="POST",
                headers=integration_auth_headers,
                timeout=10.0,
            )
            assert status == 200
            assert tested["success"] is True

            status, schema_body, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/connections/{connection['id']}/tables",
                headers=integration_auth_headers,
                timeout=10.0,
            )
            assert status == 200
            assert seeded["table_name"] in schema_body["tables"]
            table_columns = {col["name"] for col in schema_body["tables"][seeded["table_name"]]}
            assert {"id", "city", "score"} <= table_columns

            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration MySQL Preview",
                project_type="estrai",
                canvas=_estrai_mysql_canvas(seeded["table_name"]),
                default_connection_id=connection["id"],
            ) as created:
                project_id = created["metadata"]["id"]
                status, task_created, _ = http_json_response(
                    f"{integration_backend_server['base_url']}/api/projects/{project_id}/preview?limit=5",
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
                assert final_status["status"] == "success"
                result = final_status["result"]
                assert result["success"] is True
                assert result["preview_count"] == 2
                assert len(result["rows"]) == 2
                assert result["rows"][0]["city"] == "Rome"
                assert result["rows"][1]["city"] == "Milan"
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
