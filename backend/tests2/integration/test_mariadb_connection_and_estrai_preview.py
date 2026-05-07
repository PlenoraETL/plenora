"""Integration MariaDB: connection reale + schema introspection + preview ESTRAI."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.mariadb import (
    MARIADB_DATABASE,
    MARIADB_HOST,
    MARIADB_PASSWORD,
    MARIADB_PORT,
    MARIADB_USER,
    require_mariadb_enabled,
    seeded_mariadb_table,
)
from tests2.integration.harness.projects import managed_project


def _create_mariadb_connection(base_url: str, auth_headers: dict) -> dict:
    require_mariadb_enabled()
    payload = {
        "name": "Integration MariaDB Connection",
        "description": "tests2 mariadb integration",
        "type": "mysql",
        "host": MARIADB_HOST,
        "port": MARIADB_PORT,
        "database": MARIADB_DATABASE,
        "db_schema": None,
        "username": MARIADB_USER,
        "password": MARIADB_PASSWORD,
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


def _estrai_mariadb_canvas(table_name: str) -> dict:
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
                    {"name": "score", "type": "int", "visible": True},
                ],
                "filters": [],
                "calculated_columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 350, "y": 100},
                "fileName": "mariadb_preview",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }


def test_mariadb_connection_tables_and_estrai_preview_roundtrip(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
):
    _ = integration_celery_worker
    connection = _create_mariadb_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    try:
        with seeded_mariadb_table() as seeded:
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

            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration MariaDB Preview",
                project_type="estrai",
                canvas=_estrai_mariadb_canvas(seeded["table_name"]),
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
                assert final_status["result"]["preview_count"] == 2
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
