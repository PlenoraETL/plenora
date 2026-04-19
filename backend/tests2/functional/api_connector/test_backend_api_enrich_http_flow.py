"""Functional: backend + worker + fake HTTP API per api_enrich end-to-end."""

from __future__ import annotations

from socket import timeout as SocketTimeout
from urllib.request import Request, urlopen

from tests2.functional.harness.http import (
    http_json_response,
    http_multipart_response,
    poll_json_until,
)


def _http_enricher_payload(fake_api_server) -> dict:
    return {
        "name": "Functional HTTP Enricher",
        "description": "tests2 functional api_enrich http",
        "api_type": "enricher",
        "category": "functional",
        "connection_type": "enricher",
        "url": f"{fake_api_server['base_url']}/json/enrich",
        "method": "GET",
        "auth": {"type": "none"},
        "parameters": [],
        "static_parameters": {},
        "output_mapping": [
            {"path": "enriched", "column": "enriched"},
            {"path": "country_echo", "column": "country_echo"},
        ],
        "custom_headers": {},
        "options": {"rate_limit": 0},
    }


def _arricchisci_http_canvas(file_source_id: str, api_id: str) -> dict:
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
                "id": "ae-1",
                "type": "api_enrich",
                "position": {"x": 280, "y": 100},
                "api_id": api_id,
                "param_mapping": {
                    "city": "city",
                    "country": "country",
                },
                "output_mapping": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 500, "y": 100},
                "fileName": "arricchisci_http_functional",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "fs-1", "target": "ae-1", "join_type": "left"},
            {"id": "edge-2", "source": "ae-1", "target": "result-1", "join_type": "output"},
        ],
    }


def test_arricchisci_http_api_enrich_end_to_end(
    backend_server,
    celery_worker,
    fake_api_server,
    auth_headers,
):
    _ = celery_worker

    status, api_connection, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=_http_enricher_payload(fake_api_server),
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    api_id = api_connection["id"]

    status, tested, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections/{api_id}/test",
        method="POST",
        payload={"test_params": {"city": "Rome", "country": "IT"}},
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    assert tested["success"] is True

    status, uploaded, _ = http_multipart_response(
        f"{backend_server['base_url']}/api/files/upload",
        data={
            "name": "Functional HTTP CSV Source",
            "description": "tests2 functional api_enrich http",
        },
        files={
            "file": ("functional_http.csv", b"city,country\nRome,IT\nMilan,IT\n", "text/csv"),
        },
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    file_source_id = uploaded["id"]

    status, created_project, _ = http_json_response(
        f"{backend_server['base_url']}/api/projects",
        method="POST",
        payload={
            "name": "Functional Arricchisci HTTP",
            "description": "tests2 functional api_enrich http",
            "project_type": "arricchisci",
            "default_connection_id": None,
            "canvas": _arricchisci_http_canvas(file_source_id, api_id),
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
            timeout=180.0,
            interval=2.0,
            request_timeout=10.0,
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["status"] == "success"
        assert final_status["result"]["success"] is True
        assert final_status["result"]["result_id"]

        req = Request(
            f"{backend_server['base_url']}/api/tasks/{task_id}/download",
            headers=auth_headers,
            method="GET",
        )
        with urlopen(req, timeout=10.0) as resp:
            payload = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "city" in payload
            assert "country" in payload
            assert "enriched" in payload
            assert "country_echo" in payload
            assert "Rome-ok" in payload
            assert "Milan-ok" in payload
            assert "IT" in payload
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
                f"{backend_server['base_url']}/api/files/{file_source_id}",
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
