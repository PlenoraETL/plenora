"""Integration SCHEDULER: create schedule, run now, list runs, download output."""

from __future__ import annotations

from urllib.request import Request, urlopen

from tests2.functional.harness.http import http_json_response, poll_json_until
from tests2.integration.harness.file_sources import seed_csv_file_source
from tests2.integration.harness.projects import managed_project


def _arricchisci_passthrough_canvas(file_source_id: str) -> dict:
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
                "position": {"x": 350, "y": 100},
                "fileName": "scheduler_arricchisci_output",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "fs-1", "target": "result-1", "join_type": "output"}
        ],
    }


def test_scheduler_manual_run_lists_and_downloads_archived_output(
    integration_task_worker,
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
):
    _ = integration_task_worker
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
        name="Integration Scheduler Project",
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
                "description": "tests2 scheduler manual run",
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
                predicate=lambda body: isinstance(body, dict)
                and body.get("status") in ("success", "failure"),
            )
            assert final_status["status"] == "success"
            assert final_status["result"]["success"] is True

            runs = poll_json_until(
                f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}/runs",
                headers=integration_auth_headers,
                timeout=60.0,
                interval=2.0,
                predicate=lambda body: isinstance(body, dict)
                and body.get("runs_count", 0) >= 1,
            )
            assert runs["schedule_id"] == schedule_id
            assert runs["project_id"] == project_id
            run_token = runs["runs"][0]["timestamp"]
            assert runs["runs"][0]["files"]

            req = Request(
                f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}/download?run={run_token}",
                headers=integration_auth_headers,
                method="GET",
            )
            with urlopen(req, timeout=10.0) as resp:
                payload = resp.read().decode("utf-8")
                assert resp.status == 200
                assert "Rome" in payload
                assert "Milan" in payload
        finally:
            status, _, _ = http_json_response(
                f"{integration_backend_server['base_url']}/api/schedules/{schedule_id}",
                method="DELETE",
                headers=integration_auth_headers,
                timeout=10.0,
            )
            # DELETE /api/schedules/{id} risponde 204 (router status_code=204)
            assert status in (200, 204, 404)
