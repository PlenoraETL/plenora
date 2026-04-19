"""Primi integration test: login reale e persistenza DB reale."""

from __future__ import annotations

from tests2.functional.harness.http import http_json_response


def test_login_and_me_roundtrip(integration_backend_server, integration_user_seed):
    status, body, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/auth/login",
        method="POST",
        payload={
            "username": integration_user_seed["username"],
            "password": integration_user_seed["password"],
        },
        timeout=10.0,
    )
    assert status == 200
    assert body["username"] == integration_user_seed["username"]
    assert body["group_id"] == integration_user_seed["group_id"]
    assert body["role"] == "admin"

    status, me, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
        timeout=10.0,
    )
    assert status == 200
    assert me["username"] == integration_user_seed["username"]
    assert me["group_id"] == integration_user_seed["group_id"]


def test_create_api_connection_persists_to_postgres(
    integration_backend_server,
    integration_auth_headers,
    integration_user_seed,
    integration_db_probe,
):
    payload = {
        "name": "Integration Persisted API",
        "description": "tests2 integration",
        "api_type": "rest",
        "category": "integration",
        "connection_type": "enricher",
        "url": "https://example.test/health",
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
        payload=payload,
        headers=integration_auth_headers,
        timeout=10.0,
    )
    assert status == 200
    assert created["name"] == payload["name"]
    assert created["group_id"] == integration_user_seed["group_id"]

    row = integration_db_probe["fetchone"](
        "SELECT id, group_id, created_by FROM api_connections WHERE id = %s",
        (created["id"],),
    )
    assert row is not None
    assert row["group_id"] == integration_user_seed["group_id"]
    assert row["created_by"] == integration_user_seed["user_id"]
