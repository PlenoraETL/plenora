"""Integration PostgreSQL esterno: task CARICA reale e verifica righe scritte."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.postgres_ext import postgres_ext_execute, postgres_ext_fetchall
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_postgres_connection_and_estrai_preview import (
    _create_postgres_connection,
    _delete_connection,
)


def _arricchisci_carica_postgres_canvas(file_source_id: str, connection_id: str, table_name: str) -> dict:
    return {
        "nodes": [
            {
                "id": "fs-1",
                "type": "file_source",
                "position": {"x": 100, "y": 100},
                "file_source_id": file_source_id,
                "columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 320, "y": 100},
                "fileName": "postgres_carica_integration",
                "format": "csv",
                "columns": [],
            },
            {
                "id": "carica-1",
                "type": "carica",
                "position": {"x": 560, "y": 100},
                "connection_id": connection_id,
                "schema_name": "public",
                "table_name": table_name,
                "write_mode": "create",
                "column_mappings": [],
                "match_columns": [],
                "update_columns": [],
                "geometry_column": "geom",
                "geometry_srid": 4326,
                "batch_size": 1000,
                "transaction_mode": "all_or_nothing",
                "create_spatial_index": False,
                "conflict_policy": "skip",
                "if_table_exists": "error",
                "data": {
                    "connection_id": connection_id,
                    "schema_name": "public",
                    "table_name": table_name,
                    "write_mode": "create",
                    "column_mappings": [],
                    "match_columns": [],
                    "update_columns": [],
                    "geometry_column": "geom",
                    "geometry_srid": 4326,
                    "batch_size": 1000,
                    "transaction_mode": "all_or_nothing",
                    "create_spatial_index": False,
                    "conflict_policy": "skip",
                    "if_table_exists": "error",
                },
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "fs-1", "target": "result-1", "join_type": "output"},
            {"id": "edge-2", "source": "result-1", "target": "carica-1", "join_type": "carica"},
        ],
    }


def test_postgres_direct_carica_task_writes_destination_table(
    integration_task_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_task_worker
    connection = _create_postgres_connection(
        integration_backend_server["base_url"],
        integration_auth_headers,
    )
    table_name = f"it_carica_{integration_user_seed['group_id'][-8:]}"
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
            name="Integration Postgres CARICA",
            project_type="arricchisci",
            canvas=_arricchisci_carica_postgres_canvas(file_source["id"], connection["id"], table_name),
        ) as created:
            project_id = created["metadata"]["id"]
            carica_node_data = _arricchisci_carica_postgres_canvas(
                file_source["id"], connection["id"], table_name
            )["nodes"][2]

            status, task_created, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/tasks",
                method="POST",
                payload={
                    "task_type": "carica",
                    "project_id": project_id,
                    "canvas": _arricchisci_carica_postgres_canvas(
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
                timeout=120.0,
                interval=2.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert carica_status["status"] == "success"
            assert carica_status["result"]["success"] is True
            assert carica_status["result"]["rows_written"] == 2

            rows = postgres_ext_fetchall(
                f"SELECT city, country FROM public.{table_name} ORDER BY city"
            )
            assert rows == [
                {"city": "Milan", "country": "IT"},
                {"city": "Rome", "country": "IT"},
            ]
    finally:
        try:
            postgres_ext_execute(f"DROP TABLE IF EXISTS public.{table_name}")
        except Exception:
            pass
        _delete_connection(
            integration_backend_server["base_url"],
            integration_auth_headers,
            connection["id"],
        )
