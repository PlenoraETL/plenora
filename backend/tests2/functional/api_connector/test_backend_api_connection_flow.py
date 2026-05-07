"""Primo functional vero: backend + auth + fake API per api_connector."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response


def _api_connection_payload(fake_api_server) -> dict:
    return {
        "name": "Functional Fake JSON",
        "description": "tests2 functional",
        "api_type": "rest",
        "category": "functional",
        "connection_type": "enricher",
        "url": f"{fake_api_server['base_url']}/json/ok",
        "method": "GET",
        "auth": {"type": "none"},
        "parameters": [],
        "output_mapping": [],
        "custom_headers": {},
        "options": {},
    }


def test_api_connection_create_and_test_flow(backend_server, fake_api_server, auth_headers):
    status, created, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=_api_connection_payload(fake_api_server),
        headers=auth_headers,
        timeout=10.0,
    )
    # POST /api/api-connections risponde 201 Created (vedi
    # backend/app/core/arricchisci/router.py:479 status_code=201).
    assert status == 201
    assert created["name"] == "Functional Fake JSON"
    assert created["can_edit"] is True
    api_id = created["id"]

    status, tested, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections/{api_id}/test",
        method="POST",
        payload={"test_params": {}},
        headers=auth_headers,
        timeout=10.0,
    )
    assert status == 200
    assert tested["success"] is True
    assert tested["status_code"] == 200
    assert "available_fields" in tested
    assert "raw_response" in tested


def test_api_connection_test_requires_auth(backend_server, fake_api_server):
    status, created, _ = http_json_response(
        f"{backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=_api_connection_payload(fake_api_server),
        timeout=10.0,
    )
    assert status == 401
    detail = created["detail"]
    assert detail["error"] == "auth_token_missing"
    assert detail["message"] == "Token mancante"
    assert detail["details"] == {}
