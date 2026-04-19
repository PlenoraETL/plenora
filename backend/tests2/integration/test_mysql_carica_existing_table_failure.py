"""Integration MySQL: CARICA deve fallire se la tabella destinazione esiste gia'."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.mysql import mysql_execute
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_mysql_carica_roundtrip import _arricchisci_carica_mysql_canvas
from tests2.integration.test_mysql_connection_and_estrai_preview import (
    _create_mysql_connection,
    _delete_connection,
)


def test_mysql_direct_carica_task_fails_when_destination_table_already_exists(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_celery_worker
    connection = _create_mysql_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    table_name = f"it_carica_exists_{integration_user_seed['group_id'][-8:]}"
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[
            {"city": "Rome", "country": "IT"},
            {"city": "Milan", "country": "IT"},
        ],
    )

    mysql_execute(
        f"""
        CREATE TABLE `{table_name}` (
            id INT PRIMARY KEY AUTO_INCREMENT,
            city VARCHAR(255) NOT NULL,
            country VARCHAR(255) NOT NULL
        )
        """
    )

    try:
        with managed_project(
            base_url=integration_backend_server["base_url"],
            auth_headers=integration_auth_headers,
            name="Integration MySQL CARICA Existing Table",
            project_type="arricchisci",
            canvas=_arricchisci_carica_mysql_canvas(file_source["id"], connection["id"], table_name),
        ) as created:
            project_id = created["metadata"]["id"]
            carica_node_data = _arricchisci_carica_mysql_canvas(
                file_source["id"], connection["id"], table_name
            )["nodes"][2]

            status, task_created, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/tasks",
                method="POST",
                payload={
                    "task_type": "carica",
                    "project_id": project_id,
                    "canvas": _arricchisci_carica_mysql_canvas(
                        file_source["id"], connection["id"], table_name
                    ),
                    "output_format": "csv",
                    "force_recalculate": False,
                    "carica_node_data": carica_node_data,
                    "triggered_by": "manual",
                },
                headers=integration_auth_headers,
                timeout=10.0,
            )
            assert status == 200
            carica_task_id = task_created["task_id"]

            carica_status = poll_json_until(
                f"{integration_backend_server['base_url']}/api/tasks/{carica_task_id}",
                headers=integration_auth_headers,
                timeout=180.0,
                interval=2.0,
                request_timeout=10.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert carica_status["status"] == "failure"
            combined = (
                str(carica_status.get("error", "")) + " "
                + str(carica_status.get("result", "")) + " "
                + str(carica_status)
            ).lower()
            assert "exist" in combined or "esiste" in combined or "already" in combined or "table" in combined
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
