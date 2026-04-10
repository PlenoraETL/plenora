"""
tests/security/unit/test_auth_middleware_unit.py
=================================================
Test unitari del JWTMiddleware (auth_middleware.py righe 36-61).

Verifica che il middleware:
  - Parsi il Bearer token e inietti group_id/user_id/username/role in request.state
  - Gestisca token invalido senza bloccare (imposta valori vuoti)
  - Gestisca assenza di token (imposta valori vuoti)
  - Chiami set_log_group col group_id corretto
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

pytestmark = pytest.mark.unit

from app.core.auth.auth import create_access_token
from app.core.auth.auth_middleware import JWTMiddleware


# ─── App di test con middleware ───────────────────────────────────────────────

app = FastAPI()
app.add_middleware(JWTMiddleware)


@app.get("/test-state")
async def get_test_state(request: Request):
    return {
        "group_id": getattr(request.state, "group_id", "NOT_SET"),
        "user_id": getattr(request.state, "user_id", "NOT_SET"),
        "username": getattr(request.state, "username", "NOT_SET"),
        "role": getattr(request.state, "role", "NOT_SET"),
    }

client = TestClient(app)


def _make_token(user_id="uid1", username="admin", group_id="grp1", role="admin"):
    return create_access_token(user_id, username, group_id, role)


# =============================================================================
# Token Bearer valido
# =============================================================================

class TestMiddlewareTokenValido:

    def test_inietta_group_id(self):
        token = _make_token(group_id="grp-test")
        res = client.get("/test-state", headers={"Authorization": f"Bearer {token}"})
        assert res.json()["group_id"] == "grp-test"

    def test_inietta_user_id(self):
        token = _make_token(user_id="uid-42")
        res = client.get("/test-state", headers={"Authorization": f"Bearer {token}"})
        assert res.json()["user_id"] == "uid-42"

    def test_inietta_username(self):
        token = _make_token(username="marco")
        res = client.get("/test-state", headers={"Authorization": f"Bearer {token}"})
        assert res.json()["username"] == "marco"

    def test_inietta_role(self):
        token = _make_token(role="superadmin")
        res = client.get("/test-state", headers={"Authorization": f"Bearer {token}"})
        assert res.json()["role"] == "superadmin"

    def test_chiama_set_log_group(self):
        token = _make_token(group_id="grp-log")
        with patch("app.core.auth.auth_middleware.set_log_group") as mock_log:
            client.get("/test-state", headers={"Authorization": f"Bearer {token}"})
        mock_log.assert_called_with("grp-log")


# =============================================================================
# Token invalido
# =============================================================================

class TestMiddlewareTokenInvalido:

    def test_token_corrotto_non_blocca(self):
        res = client.get("/test-state",
                         headers={"Authorization": "Bearer token.invalido.xxx"})
        assert res.status_code == 200

    def test_token_corrotto_state_vuoto(self):
        res = client.get("/test-state",
                         headers={"Authorization": "Bearer token.invalido.xxx"})
        data = res.json()
        assert data["group_id"] == ""
        assert data["user_id"] == ""
        assert data["username"] == ""
        assert data["role"] == ""


# =============================================================================
# Nessun token
# =============================================================================

class TestMiddlewareSenzaToken:

    def test_no_header_non_blocca(self):
        res = client.get("/test-state")
        assert res.status_code == 200

    def test_no_header_state_vuoto(self):
        res = client.json()  if False else client.get("/test-state")
        data = res.json()
        assert data["group_id"] == ""
        assert data["user_id"] == ""
        assert data["username"] == ""
        assert data["role"] == ""

    def test_chiama_set_log_group_vuoto(self):
        with patch("app.core.auth.auth_middleware.set_log_group") as mock_log:
            client.get("/test-state")
        mock_log.assert_called_with("")

    def test_header_non_bearer(self):
        """Header Authorization con schema diverso da Bearer."""
        res = client.get("/test-state",
                         headers={"Authorization": "Basic dXNlcjpwYXNz"})
        data = res.json()
        assert data["group_id"] == ""
        assert data["user_id"] == ""
