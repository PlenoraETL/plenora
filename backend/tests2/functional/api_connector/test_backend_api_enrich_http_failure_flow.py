"""Functional: api_connector HTTP vero che deve fallire su upstream 422."""

from __future__ import annotations

from socket import timeout as SocketTimeout

from tests2.functional.harness.http import http_json_response, poll_json_until


def _http_generator_error_payload(fake_api_server) -> dict:
    return {
        "name": "Functional HTTP Generator Error",
        "description": "tests2 functional api_enrich failure",
        "api_type": "generator",
        "category": "functional",
        "connection_type": "enricher",
        "url": f"{fake_api_server['base_url']}/json/error",
        "method": "GET",
        "auth": {"type": "none"},
        "parameters": [],
        "static_parameters": {},
        "output_mapping": [
            {"path": "data[*].value", "column": "value"},
        ],
        "custom_headers": {},
        "options": {"rate_limit": 0},
    }


def _arricchisci_http_failure_canvas(api_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "sv-1",
                "type": "staticValues",
                "position": {"x": 100, "y": 100},
                "fields": [{"name": "seed", "value": "x"}],
            },
            {
                "id": "ae-1",
                "type": "api_enrich",
                "position": {"x": 280, "y": 100},
                "api_id": api_id,
                "param_mapping": {},
                "output_mapping": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 500, "y": 100},
                "fileName": "arricchisci_http_failure_functional",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "sv-1", "target": "ae-1", "join_type": "left"},
            {"id": "edge-2", "source": "ae-1", "target": "result-1", "join_type": "output"},
        ],
    }


def test_arricchisci_http_api_generator_failure_surfaces_task_error(
    backend_server,
    celery_worker,
    fake_api_server,
    auth_headers,
):
    _ = celery_worker

    status, api_connection, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=_http_generator_error_payload(fake_api_server),
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    api_id = api_connection["id"]

    status, tested, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections/{api_id}/test",
        method="POST",
        payload={"test_params": {}},
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    assert tested["success"] is False

    status, created_project, _ = http_json_response(
        f"{backend_server['base_url']}/api/projects",
        method="POST",
        payload={
            "name": "Functional Arricchisci HTTP Failure",
            "description": "tests2 functional api_enrich failure",
            "project_type": "arricchisci",
            "default_connection_id": None,
            "canvas": _arricchisci_http_failure_canvas(api_id),
        },
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    project_id = created_project["metadata"]["id"]

    try:
        status, task_created, _ = http_json_response(
            f"{backend_server['base_url']}/api/projects/{project_id}/execute",
            method="POST",
            payload={"force_recalculate": False},
            headers=auth_headers,
            timeout=10.0,
        )
        assert status == 200
        task_id = task_created["task_id"]

        final_status = poll_json_until(
            f"{backend_server['base_url']}/api/tasks/{task_id}",
            headers=auth_headers,
            timeout=120.0,
            interval=2.0,
            request_timeout=10.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["status"] == "failure"
        combined = (
            str(final_status.get("error", "")) + " "
            + str(final_status.get("result", "")) + " "
            + str(final_status)
        ).lower()
        assert "http 422" in combined or "invalid payload" in combined

        status, body, _ = http_json_response(
            f"{backend_server['base_url']}/api/tasks/{task_id}/download",
            headers=auth_headers,
            timeout=10.0,
        )
        assert status == 400
        assert "non completato" in str(body).lower()
    finally:
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/projects/{project_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            assert status in (200, 404)
        except (TimeoutError, SocketTimeout):
            pass
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/api-connections/{api_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            assert status in (200, 404, 409)
        except (TimeoutError, SocketTimeout):
            pass
