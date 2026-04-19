"""Integration test — /auth/logout + blacklist Redis end-to-end.

A differenza dei test unit in tests2/security/unit/test_jwt_logout_blacklist.py
(FastAPI TestClient in-process con fakeredis iniettato), qui:

  - il backend gira come processo uvicorn separato (fixture
    `integration_backend_server`)
  - Redis e' quello reale (container dev, gia' usato da Celery)
  - il DB e' Postgres reale con utente creato in `integration_user_seed`

Copertura end-to-end:
  1. login → token con jti valido
  2. /auth/me con token → 200
  3. /auth/logout → 200 + blacklisted=True
  4. /auth/me con STESSO token → 401 (token revocato)
  5. nuovo login → nuovo token → 200 (jti indipendente)

Valida l'integrazione:
  - auth.create_access_token emette jti
  - auth.get_current_user consulta Redis reale per is_jti_blacklisted
  - /auth/logout scrive in Redis reale con TTL corretto
"""

from __future__ import annotations

import base64
import json

import pytest

from tests2.functional.harness.http import http_json_response


pytestmark = [pytest.mark.integration, pytest.mark.security]


def _decode_jwt_payload_without_verify(token: str) -> dict:
    """Decode JWT payload senza verifica firma — usato solo per ispezionare
    il jti nel test (non per validazione)."""
    parts = token.split(".")
    assert len(parts) == 3, f"JWT malformato: {token!r}"
    raw = parts[1]
    # padding base64url
    raw += "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    return json.loads(decoded)


def _auth_get_me(base_url: str, token: str):
    status, body, _ = http_json_response(
        f"{base_url}/api/auth/me",
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    return status, body


def _auth_logout(base_url: str, token: str):
    status, body, _ = http_json_response(
        f"{base_url}/api/auth/logout",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        payload=None,
        timeout=10.0,
    )
    return status, body


def _auth_login(base_url: str, username: str, password: str):
    status, body, _ = http_json_response(
        f"{base_url}/api/auth/login",
        method="POST",
        payload={"username": username, "password": password},
        timeout=10.0,
    )
    return status, body


def test_logout_revokes_token_end_to_end(
    integration_backend_server, integration_user_seed
):
    """Dopo /auth/logout, lo stesso token non e' piu' utilizzabile."""
    base_url = integration_backend_server["base_url"]
    username = integration_user_seed["username"]
    password = integration_user_seed["password"]

    # 1. Login reale
    status, login_body = _auth_login(base_url, username, password)
    assert status == 200, f"login fallito: {status} {login_body}"
    token = login_body["access_token"]

    # Verifica che il token contenga un jti (non vuoto)
    payload = _decode_jwt_payload_without_verify(token)
    assert "jti" in payload, f"Token senza jti: {payload}"
    assert isinstance(payload["jti"], str) and len(payload["jti"]) >= 16

    # 2. /auth/me → 200 (token valido)
    status_me, body_me = _auth_get_me(base_url, token)
    assert status_me == 200, f"auth/me pre-logout atteso 200, got {status_me}: {body_me}"
    assert body_me["username"] == username

    # 3. /auth/logout → 200, blacklisted=True
    status_logout, body_logout = _auth_logout(base_url, token)
    assert status_logout == 200, f"logout atteso 200, got {status_logout}: {body_logout}"
    assert body_logout["ok"] is True
    assert body_logout["blacklisted"] is True
    assert body_logout["ttl"] > 0

    # 4. /auth/me con stesso token → 401 (revocato via blacklist Redis)
    status_after, body_after = _auth_get_me(base_url, token)
    assert status_after == 401, (
        f"auth/me post-logout atteso 401 (token revocato), "
        f"got {status_after}: {body_after}"
    )


def test_new_login_after_logout_issues_independent_token(
    integration_backend_server, integration_user_seed
):
    """Dopo il logout, un nuovo login emette un jti diverso e funzionante.

    Regressione: se la blacklist colpisse per sub/user invece che per jti,
    il nuovo token sarebbe falsamente rifiutato.
    """
    base_url = integration_backend_server["base_url"]
    username = integration_user_seed["username"]
    password = integration_user_seed["password"]

    # Primo login + logout
    status, body = _auth_login(base_url, username, password)
    assert status == 200
    old_token = body["access_token"]
    old_jti = _decode_jwt_payload_without_verify(old_token)["jti"]

    _auth_logout(base_url, old_token)

    # Nuovo login
    status2, body2 = _auth_login(base_url, username, password)
    assert status2 == 200
    new_token = body2["access_token"]
    new_jti = _decode_jwt_payload_without_verify(new_token)["jti"]

    assert new_jti != old_jti, "Nuovo token ha lo stesso jti del precedente"

    # Il nuovo token deve funzionare
    status_me, _ = _auth_get_me(base_url, new_token)
    assert status_me == 200, "Nuovo token rifiutato — blacklist colpisce per sub?"

    # Il vecchio token resta bannato
    status_old, _ = _auth_get_me(base_url, old_token)
    assert status_old == 401


def test_logout_without_bearer_returns_401(integration_backend_server):
    """Endpoint /auth/logout richiede un bearer token."""
    base_url = integration_backend_server["base_url"]
    status, _body, _ = http_json_response(
        f"{base_url}/api/auth/logout",
        method="POST",
        payload=None,
        timeout=10.0,
    )
    assert status == 401
