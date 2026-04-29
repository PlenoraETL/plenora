"""Integration: backend + Postgres reale + fake API esterna."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response


def test_api_connection_test_calls_fake_api_and_preserves_record(
    integration_backend_server,
    integration_fake_api_server,
    integration_auth_headers,
    integration_user_seed,
    integration_db_probe,
):
    """Il fake server locale gira su 127.0.0.1 e quindi, correttamente,
    la policy anti-SSRF lo blocca. Il contratto utile qui e' che:
    - la connection si crea e si persiste in DB
    - il test API non esplode lato backend
    - il risultato applicativo e' `success=False` con errore di URL bloccato
    """
    create_payload = {
        "name": "Integration Fake API Call",
        "description": "tests2 integration external",
        "api_type": "rest",
        "category": "integration",
        "connection_type": "enricher",
        "url": f"{integration_fake_api_server['base_url']}/json/ok",
        "method": "GET",
        "auth": {"type": "none"},
        "parameters": [],
        "output_mapping": [],
        "custom_headers": {},
        "options": {},
    }

    status, created, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/api-connections",
        method="POST",
        payload=create_payload,
        headers=integration_auth_headers,
        timeout=10.0,
    )
    # POST /api/api-connections risponde 201 Created (router status_code=201)
    assert status == 201
    api_id = created["id"]

    status, tested, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/api-connections/{api_id}/test",
        method="POST",
        payload={"test_params": {}},
        headers=integration_auth_headers,
        timeout=10.0,
    )
    assert status == 200
    assert tested["success"] is False
    assert "URL bloccato" in tested["error"]

    row = integration_db_probe["fetchone"](
        "SELECT id, group_id, created_by FROM api_connections WHERE id = %s",
        (api_id,),
    )
    assert row is not None
    assert row["group_id"] == integration_user_seed["group_id"]
    assert row["created_by"] == integration_user_seed["user_id"]
