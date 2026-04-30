"""Integration: contract test sulle shape HTTP piu' esposte al frontend."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.projects import managed_project
from tests2.integration.test_scheduler_manual_run_roundtrip import (
    _arricchisci_passthrough_canvas,
)


def _full_rest_file_api_connection_payload(base_url: str) -> dict:
    return {
        "name": "HTTP Shape REST File",
        "description": "tests2 response shape",
        "api_type": "enricher",
        "category": "integration",
        "connection_type": "rest_file",
        "url": f"{base_url}/multipart/echo",
        "method": "POST",
        "auth": {"type": "none"},
        "custom_headers": {"X-Test": "shape"},
        "parameters": [{"name": "city", "type": "string", "default_value": "Rome"}],
        "static_parameters": {"country": "IT"},
        "output_mapping": [{"path": "status", "column": "status"}],
        "schema_fields": [{"name": "status", "type": "string"}],
        "response_preset": "",
        "response_config": {"mappings": [{"path": "status", "column": "status"}]},
        "pagination": {"type": "none"},
        "options": {"continue_on_error": False},
        "batch": {"enabled": False},
        "request": {
            "body_type": "multipart",
            "timeout": 30,
            "file_fields": [{"name": "file", "source": "node_input", "required": True}],
            "static_fields": [{"name": "mode", "value": "echo"}],
        },
        "response": {
            "format": "binary_file",
            "output_extension": ".zip",
        },
    }


def test_get_api_connection_response_has_expected_shape(
    integration_backend_server,
    integration_fake_api_server,
    integration_auth_headers,
):
    status, created, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=_full_rest_file_api_connection_payload(
            integration_fake_api_server["base_url"]
        ),
        headers=integration_auth_headers,
        timeout=10.0,
    )
    # POST /api/api-connections risponde 201 Created (router status_code=201)
    assert status == 201
    api_id = created["id"]

    status, body, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/api-connections/{api_id}",
        headers=integration_auth_headers,
        timeout=10.0,
    )
    assert status == 200
    assert set(body.keys()) >= {
        "id",
        "name",
        "description",
        "api_type",
        "category",
        "connection_type",
        "endpoint",
        "auth",
        "custom_headers",
        "parameters",
        "static_parameters",
        "output_mapping",
        "schema_fields",
        "response_preset",
        "response_config",
        "pagination",
        "options",
        "batch",
        "request",
        "response",
        "created_at",
        "updated_at",
        "group_id",
        "created_by",
        "can_edit",
    }
    assert body["id"] == api_id
    assert body["connection_type"] == "rest_file"
    assert body["endpoint"]["method"] == "POST"
    assert body["request"]["timeout"] == 30
    assert body["request"]["file_fields"][0]["name"] == "file"
    assert body["response"]["format"] == "binary_file"
    assert body["can_edit"] is True


def test_get_project_response_has_expected_shape(
    integration_backend_server,
    integration_auth_headers,
):
    canvas = {
        "nodes": [
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 100, "y": 100},
                "data": {},
                "fileName": "shape_contract",
                "format": "csv",
                "columns": [],
            }
        ],
        "edges": [],
    }

    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="HTTP Shape Project",
        project_type="estrai",
        canvas=canvas,
    ) as created:
        project_id = created["metadata"]["id"]

        status, body, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}",
            headers=integration_auth_headers,
            timeout=10.0,
        )
        assert status == 200
        assert set(body.keys()) == {"metadata", "canvas", "columns_metadata"}
        assert set(body["metadata"].keys()) >= {
            "id",
            "name",
            "project_type",
            "group_id",
            "created_at",
            "updated_at",
        }
        assert body["metadata"]["id"] == project_id
        assert body["canvas"]["nodes"][0]["id"] == "result-1"
        assert body["canvas"]["nodes"][0]["fileName"] == "shape_contract"
        assert isinstance(body["columns_metadata"], list)


def test_get_task_status_response_has_expected_shape(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_celery_worker
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[{"city": "Rome", "country": "IT"}],
    )

    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="HTTP Shape Task Project",
        project_type="arricchisci",
        canvas=_arricchisci_passthrough_canvas(file_source["id"]),
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

        body = poll_json_until(
            f"{integration_backend_server['base_url']}/api/tasks/{task_id}",
            headers=integration_auth_headers,
            timeout=120.0,
            interval=2.0,
            request_timeout=10.0,
            predicate=lambda payload: isinstance(payload, dict)
            and payload.get("status") in ("success", "failure"),
        )

        assert set(body.keys()) == {
            "task_id",
            "task_type",
            "project_id",
            "status",
            "progress",
            "message",
            "result",
            "error",
            "created_at",
            "started_at",
            "completed_at",
        }
        assert body["task_id"] == task_id
        assert body["project_id"] == project_id
        assert isinstance(body["progress"], int)
        assert body["status"] == "success"
        assert set(body["result"].keys()) >= {
            "success",
            "result_id",
            "rows_count",
            "output_filename",
        }


def test_schedule_runs_response_has_expected_shape(
    integration_celery_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_celery_worker
    file_source = seed_csv_file_source(
        group_id=integration_user_seed["group_id"],
        created_by=integration_user_seed["user_id"],
        rows=[
            {"city": "Rome", "country": "IT"},
            {"city": "Milan", "country": "IT"},
        ],
    )

    with managed_project(
        base_url=integration_backend_server["base_url"],
        auth_headers=integration_auth_headers,
        name="HTTP Shape Schedule Project",
        project_type="arricchisci",
        canvas=_arricchisci_passthrough_canvas(file_source["id"]),
    ) as created:
        project_id = created["metadata"]["id"]

        status, schedule_created, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/schedules",
            method="POST",
            payload={
                "project_id": project_id,
                "cron_expression": "0 3 * * *",
                "timezone": "Europe/Rome",
                "description": "tests2 schedule shape",
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
        # POST /api/schedules risponde 201 Created (router status_code=201)
        assert status == 201
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
                request_timeout=10.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert final_status["status"] == "success"

            runs = poll_json_until(
                f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}/runs",
                headers=integration_auth_headers,
                timeout=60.0,
                interval=2.0,
                request_timeout=10.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("runs_count", 0) >= 1,
            )
            assert set(runs.keys()) == {
                "schedule_id",
                "project_id",
                "runs_count",
                "runs",
            }
            assert runs["schedule_id"] == schedule_id
            assert runs["project_id"] == project_id
            assert runs["runs_count"] >= 1
            first_run = runs["runs"][0]
            assert set(first_run.keys()) == {"timestamp", "files", "total_size"}
            assert isinstance(first_run["timestamp"], str)
            assert isinstance(first_run["files"], list)
            assert isinstance(first_run["total_size"], int)
            first_file = first_run["files"][0]
            assert set(first_file.keys()) == {"name", "size_bytes", "size_human"}
        finally:
            status, _, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}",
                method="DELETE",
                headers=integration_auth_headers,
                timeout=10.0,
            )
            # DELETE /api/schedules/{id} risponde 204 (router status_code=204)
            assert status in (200, 204, 404)


def test_get_schedule_response_has_expected_shape(
    integration_backend_server,
    integration_auth_headers,
):
    status, project_created, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/projects",
        method="POST",
        payload={
            "name": "HTTP Shape Schedule Detail Project",
            "description": "tests2 schedule shape",
            "project_type": "estrai",
            "default_connection_id": None,
            "canvas": {
                "nodes": [
                    {
                        "id": "result-1",
                        "type": "result",
                        "position": {"x": 100, "y": 100},
                        "data": {},
                        "fileName": "shape_schedule",
                        "format": "csv",
                        "columns": [],
                    }
                ],
                "edges": [],
            },
        },
        headers=integration_auth_headers,
        timeout=10.0,
    )
    # POST /api/projects risponde 201 Created (router status_code=201)
    assert status == 201
    project_id = project_created["metadata"]["id"]

    status, schedule_created, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/schedules",
        method="POST",
        payload={
            "project_id": project_id,
            "cron_expression": "0 3 * * *",
            "timezone": "Europe/Rome",
            "description": "tests2 schedule detail shape",
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
    # POST /api/schedules risponde 201 Created (router status_code=201)
    assert status == 201
    schedule_id = schedule_created["id"]

    try:
        status, body, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}",
            headers=integration_auth_headers,
            timeout=10.0,
        )
        assert status == 200
        assert set(body.keys()) >= {
            "id",
            "created_at",
            "updated_at",
            "pipeline",
            "schedule",
            "execution",
            "on_error",
            "conflict_policy",
            "concurrency",
            "pre_check",
            "runtime",
            "destination",
        }
        assert body["id"] == schedule_id
        assert body["pipeline"]["project_id"] == project_id
        assert "cron_expression" in body["schedule"]
        assert "status" in body["runtime"]
        assert body["destination"] is None
    finally:
        status, _, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}",
            method="DELETE",
            headers=integration_auth_headers,
            timeout=10.0,
        )
        # DELETE /api/schedules/{id} risponde 204 (router status_code=204)
        assert status in (200, 204, 404)
        status, _, _ = http_json_response(
            f"{integration_backend_server['base_url']}/api/projects/{project_id}",
            method="DELETE",
            headers=integration_auth_headers,
            timeout=10.0,
        )
        # DELETE /api/projects/{id} risponde 204 (F2-B2 2026-04-24)
        assert status in (200, 204, 404)
