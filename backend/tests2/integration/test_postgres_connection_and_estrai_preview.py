"""Integration PostgreSQL esterno: connection reale + preview ESTRAI."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.canvas_builders import build_estrai_preview_canvas
from tests2.integration.harness.postgres_ext import (
    postgres_ext_connection_payload,
    seeded_postgres_ext_table,
)
from tests2.integration.harness.projects import managed_project


def _create_postgres_connection(base_url: str, auth_headers: dict) -> dict:
    payload = {
        "name": "Integration Postgres Connection",
        "description": "tests2 postgres integration",
        "type": "postgres",
        **postgres_ext_connection_payload(),
    }
    status, created, _ = http_json_response(
        f"{base_url}/api/connections",
        method="POST",
        payload=payload,
        headers=auth_headers,
        timeout=10.0,
    )
    # POST /api/connections risponde 201 Created
    # (backend/app/core/connections/router.py:62 status_code=201)
    assert status == 201
    return created


def _delete_connection(base_url: str, auth_headers: dict, connection_id: str) -> None:
    status, _, _ = http_json_response(
        f"{base_url}/api/connections/{connection_id}",
        method="DELETE",
        headers=auth_headers,
        timeout=10.0,
    )
    # DELETE /api/connections/{id} risponde 204
    # (backend/app/core/connections/router.py:129 status_code=204)
    assert status in (200, 204, 404, 409)


def _estrai_postgres_canvas(table_name: str) -> dict:
    return build_estrai_preview_canvas(table_name, file_name="postgres_preview")


def test_postgres_connection_tables_and_estrai_preview_roundtrip(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
):
    _ = integration_celery_worker
    connection = _create_postgres_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    try:
        with seeded_postgres_ext_table() as seeded:
            status, tested, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/connections/{connection['id']}/test",
                method="POST",
                headers=integration_auth_headers,
                timeout=10.0,
            )
            assert status == 200
            assert tested["success"] is True

            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration Postgres Preview",
                project_type="estrai",
                canvas=_estrai_postgres_canvas(seeded["table_name"]),
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
