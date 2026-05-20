"""Integration MySQL: execute ESTRAI reale e download progetto."""

from __future__ import annotations

from urllib.request import Request, urlopen

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.mysql import seeded_mysql_table
from tests2.integration.test_mysql_connection_and_estrai_preview import (
    _create_mysql_connection,
    _delete_connection,
    _estrai_mysql_canvas,
)
from tests2.integration.harness.projects import managed_project


def test_mysql_execute_returns_downloadable_project_result(
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
        with seeded_mysql_table() as seeded:
            with managed_project(
                base_url=integration_backend_server["base_url"],
                auth_headers=integration_auth_headers,
                name="Integration MySQL Execute",
                project_type="estrai",
                canvas=_estrai_mysql_canvas(seeded["table_name"]),
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
                    timeout=150.0,
                    interval=2.0,
                    request_timeout=10.0,
                    predicate=lambda body: isinstance(body, dict)
                    and body.get("status") in ("success", "failure"),
                )
                assert final_status["status"] == "success"
                result = final_status["result"]
                assert result["success"] is True
                assert result["rows_count"] == 2
                assert result["output_file"] == "mysql_preview.csv"
                assert result["columns"] == ["id", "city", "score"]
                contract_columns = result["output_contract"]["columns"]
                assert [c["physical_name"] for c in contract_columns] == [
                    "id",
                    "city",
                    "score",
                ]
                assert "FROM" in result["query"]
                assert seeded["table_name"] in result["query"]

                req = Request(
                    f"{integration_backend_server['base_url']}/api/projects/{project_id}/download",
                    headers=integration_auth_headers,
                    method="GET",
                )
                with urlopen(req, timeout=10.0) as resp:
                    payload = resp.read().decode("utf-8")
                    assert resp.status == 200
                    assert "attachment;" in resp.headers["Content-Disposition"]
                    assert "id" in payload
                    assert "city" in payload
                    assert "score" in payload
                    assert "Rome" in payload
                    assert "Milan" in payload
    finally:
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
