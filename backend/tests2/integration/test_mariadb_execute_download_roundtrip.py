"""Integration MariaDB: execute ESTRAI reale + download progetto."""

from __future__ import annotations

from urllib.request import Request, urlopen

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.mariadb import seeded_mariadb_table
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_mariadb_connection_and_estrai_preview import (
    _create_mariadb_connection,
    _delete_connection,
    _estrai_mariadb_canvas,
)


def test_mariadb_execute_returns_downloadable_project_result(
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
            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration MariaDB Execute",
                project_type="estrai",
                canvas=_estrai_mariadb_canvas(seeded["table_name"]),
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
                assert final_status["status"] == "success"
                req = Request(
                    f"{integration_backend_server['base_url']}/api/projects/{project_id}/download",
                    headers=integration_auth_headers,
                    method="GET",
                )
                with urlopen(req, timeout=10.0) as resp:
                    payload = resp.read().decode("utf-8")
                    assert resp.status == 200
                    assert "Rome" in payload
                    assert "Milan" in payload
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
