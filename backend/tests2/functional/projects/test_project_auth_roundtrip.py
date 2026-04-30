"""Functional: progetto reale via backend + auth JWT."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response


def test_project_create_get_delete_roundtrip(backend_server, auth_headers):
    create_payload = {
        "name": "Functional Project Roundtrip",
        "description": "tests2 functional",
        "project_type": "estrai",
        "default_connection_id": None,
        "canvas": {"nodes": [], "edges": []},
    }

    status, created, _ = http_json_response(
        f"{backend_server['base_url']}/api/projects",
        method="POST",
        payload=create_payload,
        headers=auth_headers,
        timeout=10.0,
    )
    # POST /api/projects risponde 201 Created (router status_code=201)
    assert status == 201
    project_id = created["metadata"]["id"]
    assert created["metadata"]["project_type"] == "estrai"

    try:
        status, fetched, _ = http_json_response(
            f"{backend_server['base_url']}/api/projects/{project_id}",
            headers=auth_headers,
            timeout=10.0,
        )
        assert status == 200
        assert fetched["metadata"]["id"] == project_id
        assert fetched["canvas"]["nodes"] == []
        assert fetched["canvas"]["edges"] == []
    finally:
        status, _, _ = http_json_response(
            f"{backend_server['base_url']}/api/projects/{project_id}",
            method="DELETE",
            headers=auth_headers,
            timeout=10.0,
        )
        # DELETE /api/projects/{id} risponde 204 No Content (F2-B2 2026-04-24)
        assert status in (200, 204, 404)
