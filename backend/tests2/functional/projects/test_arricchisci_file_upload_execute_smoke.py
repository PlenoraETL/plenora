"""Functional: upload file reale + canvas ARRICCHISCI minimo + execute async.

Il canvas minimo VALIDO include un nodo api_enrich: dall'epic Canvas
Validation un progetto arricchisci senza enricher e' invalido by design
(ARRICCHISCI_NO_ENRICHER) e il gate all'execute lo blocca con 409 — il
vecchio passthrough file→result non e' piu' un flusso di prodotto.
"""

from __future__ import annotations

from socket import timeout as SocketTimeout
from urllib.request import Request, urlopen

from tests2.functional.harness.http import (
    http_json_response,
    http_multipart_response,
    poll_json_until,
)
from tests2.functional.harness.projects import validate_project_canvas


def _minimal_enricher_payload(fake_api_server) -> dict:
    return {
        "name": "Functional Smoke Enricher",
        "description": "tests2 functional arricchisci smoke",
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
        ],
        "custom_headers": {},
        "options": {"rate_limit": 0},
    }


def _arricchisci_file_canvas(file_source_id: str, api_id: str) -> dict:
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
                # Config nel formato FE (dentro `data`): il node-validator
                # legge SOLO data.api_id (top-level -> ARRICCHISCI_API_NOT_SELECTED);
                # l'executor legge entrambe le shape (get_field fallback).
                "data": {
                    "api_id": api_id,
                    "param_mapping": {"city": "city", "country": "country"},
                    "output_mapping": [],
                },
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 500, "y": 100},
                "fileName": "arricchisci_functional",
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "fs-1",
                "target": "ae-1",
                "join_type": "left",
            },
            {
                "id": "edge-2",
                "source": "ae-1",
                "target": "result-1",
                "join_type": "output",
            },
        ],
    }


def test_arricchisci_execute_from_uploaded_file_reaches_success(
    backend_server,
    task_worker,
    fake_api_server,
    auth_headers,
):
    _ = task_worker
    status, api_connection, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=_minimal_enricher_payload(fake_api_server),
        headers=auth_headers,
        timeout=10.0,
    )
    # POST /api/api-connections risponde 201 Created (router status_code=201)
    assert status == 201
    api_id = api_connection["id"]

    status, uploaded, _ = http_multipart_response(
        f"{backend_server['base_url']}/api/files/upload",
        data={
            "name": "Functional CSV Source",
            "description": "tests2 functional arricchisci",
        },
        files={
            "file": ("functional.csv", b"city,country\nRome,IT\n", "text/csv"),
        },
        headers=auth_headers,
        timeout=10.0,
    )
    # F3-B3 (2026-04-24): POST /api/files/upload ora risponde 201 Created.
    assert status == 201
    file_source_id = uploaded["id"]

    create_payload = {
        "name": "Functional Arricchisci Upload",
        "description": "tests2 functional",
        "project_type": "arricchisci",
        "default_connection_id": None,
        "canvas": _arricchisci_file_canvas(file_source_id, api_id),
    }
    status, created_project, _ = http_json_response(
        f"{backend_server['base_url']}/api/projects",
        method="POST",
        payload=create_payload,
        headers=auth_headers,
        timeout=10.0,
    )
    # F3-B2 (2026-04-24): POST /api/projects ora risponde 201 Created.
    assert status == 201
    project_id = created_project["metadata"]["id"]

    # Validate-first: senza, /execute e' 409 fail-fast (gate canvas).
    validate_project_canvas(backend_server["base_url"], project_id, auth_headers)

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
            predicate=lambda body: isinstance(body, dict)
            and body.get("status") in ("success", "failure"),
        )
        assert final_status["status"] == "success"
        assert final_status["project_id"] == project_id
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
            assert "Rome" in payload
    finally:
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/projects/{project_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            # DELETE /api/projects/{id} risponde 204 No Content (F2-B2 2026-04-24)
            assert status in (200, 204, 404)
        except (TimeoutError, SocketTimeout):
            pass
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/files/{file_source_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            # DELETE /api/files/{id} accetta 200/204/404 per cleanup idempotency
            assert status in (200, 204, 404)
        except (TimeoutError, SocketTimeout):
            pass
        try:
            status, _, _ = http_json_response(
                f"{backend_server['base_url']}/api/api-connections/{api_id}",
                method="DELETE",
                headers=auth_headers,
                timeout=30.0,
            )
            # DELETE /api/api-connections/{id} risponde 204 (router status_code=204)
            assert status in (200, 204, 404, 409)
        except (TimeoutError, SocketTimeout):
            pass
