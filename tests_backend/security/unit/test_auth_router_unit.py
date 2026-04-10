"""
tests/security/unit/test_auth_router_unit.py
=============================================
Test unitari degli endpoint del router auth con mock DB.

Copertura:
  - POST /auth/login (tutti i branch)
  - GET  /auth/me
  - POST /auth/change-password
  - GET  /auth/users (admin only)
  - POST /auth/users (admin only)
  - DELETE /auth/users/{id} (admin only)
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

from app.core.auth.auth import (
    router, hash_password, create_access_token,
    SECRET_KEY, ALGORITHM, CurrentUser,
    _failed_attempts, _rate_lock,
)
import jwt as pyjwt


# ─── App di test ──────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(router)
client = TestClient(app, raise_server_exceptions=True)


def _make_token(user_id="uid1", username="admin", group_id="grp1", role="admin",
                hours=8) -> str:
    return create_access_token(user_id, username, group_id, role)


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mock_user(user_id="uid1", username="admin", group_id="grp1",
               role="admin", is_active=True, password="admin123"):
    return {
        "id": user_id,
        "username": username,
        "email": f"{username}@test.com",
        "group_id": group_id,
        "role": role,
        "is_active": is_active,
        "password_hash": hash_password(password),
        "created_at": "2025-01-01T00:00:00",
    }


def _reset_rate_limit(ip="testclient"):
    with _rate_lock:
        _failed_attempts.pop(ip, None)


# =============================================================================
# POST /auth/login
# =============================================================================

class TestLoginEndpoint:

    def setup_method(self):
        _reset_rate_limit()

    def test_login_valido_200(self):
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "admin123"})
        assert res.status_code == 200

    def test_login_ritorna_access_token(self):
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "admin123"})
        assert "access_token" in res.json()

    def test_login_ritorna_username(self):
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "admin123"})
        assert res.json()["username"] == "admin"

    def test_login_ritorna_group_id(self):
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "admin123"})
        assert res.json()["group_id"] == "grp1"

    def test_login_ritorna_expires_in(self):
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "admin123"})
        assert res.json()["expires_in"] > 0

    def test_login_password_sbagliata_401(self):
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "sbagliata"})
        assert res.status_code == 401

    def test_login_utente_non_trovato_401(self):
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = None
            res = client.post("/auth/login",
                              json={"username": "nonexist", "password": "pass"})
        assert res.status_code == 401

    def test_login_utente_disabilitato_403(self):
        user = _mock_user(is_active=False)
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user_by_username.return_value = user
            res = client.post("/auth/login",
                              json={"username": "admin", "password": "admin123"})
        assert res.status_code == 403

    def test_login_body_mancante_422(self):
        res = client.post("/auth/login", json={})
        assert res.status_code == 422

    def test_login_username_mancante_422(self):
        res = client.post("/auth/login", json={"password": "pass"})
        assert res.status_code == 422

    def test_login_password_mancante_422(self):
        res = client.post("/auth/login", json={"username": "admin"})
        assert res.status_code == 422

    def test_login_resetta_tentativi_falliti(self):
        """Login riuscito → _clear_failed_logins chiamato."""
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.core.auth.auth._clear_failed_logins") as mock_clear:
            mock_db.get_user_by_username.return_value = user
            client.post("/auth/login",
                        json={"username": "admin", "password": "admin123"})
        mock_clear.assert_called_once()

    def test_login_fallito_registra_tentativo(self):
        """Login fallito → _record_failed_login chiamato."""
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.core.auth.auth._record_failed_login") as mock_rec:
            mock_db.get_user_by_username.return_value = user
            client.post("/auth/login",
                        json={"username": "admin", "password": "sbagliata"})
        mock_rec.assert_called_once()


# =============================================================================
# GET /auth/me
# =============================================================================

class TestMeEndpoint:

    def test_me_200(self):
        token = _make_token()
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.get("/auth/me", headers=_auth_headers(token))
        assert res.status_code == 200

    def test_me_ritorna_username(self):
        token = _make_token()
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.get("/auth/me", headers=_auth_headers(token))
        assert res.json()["username"] == "admin"

    def test_me_senza_token_401(self):
        res = client.get("/auth/me")
        assert res.status_code == 401

    def test_me_token_invalido_401(self):
        res = client.get("/auth/me",
                         headers={"Authorization": "Bearer token.invalido"})
        assert res.status_code == 401

    def test_me_utente_disabilitato_401(self):
        token = _make_token()
        user = _mock_user(is_active=False)
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.get("/auth/me", headers=_auth_headers(token))
        assert res.status_code == 401

    def test_me_ritorna_group_id(self):
        token = _make_token(group_id="grp-speciale")
        user = _mock_user(group_id="grp-speciale")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.get("/auth/me", headers=_auth_headers(token))
        assert res.json()["group_id"] == "grp-speciale"

    def test_me_non_ritorna_password_hash(self):
        token = _make_token()
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.get("/auth/me", headers=_auth_headers(token))
        assert "password_hash" not in res.json()


# =============================================================================
# POST /auth/change-password
# =============================================================================

class TestChangePassword:

    def test_change_password_ok_200(self):
        token = _make_token()
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.update_user_password.return_value = None
            res = client.post("/auth/change-password",
                              json={"old_password": "admin123", "new_password": "nuova123"},
                              headers=_auth_headers(token))
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_change_password_vecchia_sbagliata_400(self):
        token = _make_token()
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.post("/auth/change-password",
                              json={"old_password": "sbagliata", "new_password": "nuova"},
                              headers=_auth_headers(token))
        assert res.status_code == 400

    def test_change_password_senza_token_401(self):
        res = client.post("/auth/change-password",
                          json={"old_password": "old", "new_password": "new"})
        assert res.status_code == 401

    def test_change_password_chiama_update(self):
        token = _make_token()
        user = _mock_user()
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.update_user_password.return_value = None
            client.post("/auth/change-password",
                        json={"old_password": "admin123", "new_password": "nuova123"},
                        headers=_auth_headers(token))
        mock_db.update_user_password.assert_called_once()


# =============================================================================
# GET /auth/users (solo admin)
# =============================================================================

class TestListUsers:

    def test_list_users_admin_200(self):
        token = _make_token(role="admin")
        user = _mock_user(role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.list_users.return_value = [user]
            mock_db.list_groups.return_value = []
            res = client.get("/auth/users", headers=_auth_headers(token))
        assert res.status_code == 200

    def test_list_users_user_200(self):
        """User può vedere gli utenti del proprio gruppo."""
        token = _make_token(role="user")
        user = _mock_user(role="user")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.list_users.return_value = [user]
            mock_db.list_groups.return_value = []
            res = client.get("/auth/users", headers=_auth_headers(token))
        assert res.status_code == 200

    def test_list_users_superadmin_200(self):
        token = _make_token(role="superadmin")
        user = _mock_user(role="superadmin")
        grp = {"id": "grp1", "name": "Gruppo 1", "description": ""}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.list_groups.return_value = [grp]
            mock_db.list_users.return_value = [user]
            res = client.get("/auth/users", headers=_auth_headers(token))
        assert res.status_code == 200

    def test_list_users_senza_token_401(self):
        res = client.get("/auth/users")
        assert res.status_code == 401

    def test_list_users_ritorna_lista(self):
        token = _make_token(role="admin")
        user = _mock_user(role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.list_users.return_value = [user]
            mock_db.list_groups.return_value = []
            res = client.get("/auth/users", headers=_auth_headers(token))
        assert isinstance(res.json(), list)


# =============================================================================
# POST /auth/users (solo admin)
# =============================================================================

class TestCreateUser:

    def test_create_user_admin_201(self):
        token = _make_token(role="admin")
        admin = _mock_user(role="admin")
        new_user = _mock_user(user_id="uid2", username="newuser")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            mock_db.get_user_by_username.return_value = None
            mock_db.create_user.return_value = new_user
            res = client.post("/auth/users",
                              json={"username": "newuser", "password": "pass123",
                                    "group_id": "grp1"},
                              headers=_auth_headers(token))
        assert res.status_code == 201

    def test_create_user_username_esistente_409(self):
        token = _make_token(role="admin")
        admin = _mock_user(role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            mock_db.get_user_by_username.return_value = admin  # già esiste
            res = client.post("/auth/users",
                              json={"username": "admin", "password": "pass",
                                    "group_id": "grp1"},
                              headers=_auth_headers(token))
        assert res.status_code == 409

    def test_create_user_non_admin_403(self):
        token = _make_token(role="user")
        user = _mock_user(role="user")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.post("/auth/users",
                              json={"username": "new", "password": "p", "group_id": "g"},
                              headers=_auth_headers(token))
        assert res.status_code == 403

    def test_create_user_admin_forzato_nel_proprio_gruppo(self):
        """Admin non-superadmin → group_id forzato al proprio."""
        token = _make_token(role="admin", group_id="grp1")
        admin = _mock_user(role="admin", group_id="grp1")
        new_user = _mock_user(user_id="uid2", username="newuser", group_id="grp1")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            mock_db.get_user_by_username.return_value = None
            mock_db.create_user.return_value = new_user
            client.post("/auth/users",
                        json={"username": "newuser", "password": "pass",
                              "group_id": "altro-gruppo"},
                        headers=_auth_headers(token))
        # group_id deve essere stato forzato a grp1
        call_kwargs = mock_db.create_user.call_args[1]
        assert call_kwargs["group_id"] == "grp1"


# =============================================================================
# DELETE /auth/users/{id}
# =============================================================================

class TestDeleteUser:

    def test_delete_user_ok_204(self):
        token = _make_token(role="admin", user_id="admin-uid")
        admin = _mock_user(role="admin", user_id="admin-uid")
        target = _mock_user(user_id="target-uid", username="target", group_id="grp1")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            mock_db.delete_user.return_value = True
            res = client.delete("/auth/users/target-uid",
                                headers=_auth_headers(token))
        assert res.status_code == 204

    def test_delete_se_stesso_400(self):
        token = _make_token(role="admin", user_id="admin-uid")
        admin = _mock_user(role="admin", user_id="admin-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            res = client.delete("/auth/users/admin-uid",
                                headers=_auth_headers(token))
        assert res.status_code == 400

    def test_delete_utente_non_trovato_404(self):
        token = _make_token(role="admin", user_id="admin-uid")
        admin = _mock_user(role="admin", user_id="admin-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else None
            res = client.delete("/auth/users/nonexist",
                                headers=_auth_headers(token))
        assert res.status_code == 404

    def test_delete_utente_altro_gruppo_403(self):
        token = _make_token(role="admin", user_id="admin-uid", group_id="grp1")
        admin = _mock_user(role="admin", user_id="admin-uid", group_id="grp1")
        target = _mock_user(user_id="target-uid", username="other", group_id="altro-grp")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            res = client.delete("/auth/users/target-uid",
                                headers=_auth_headers(token))
        assert res.status_code == 403

    def test_delete_non_admin_403(self):
        token = _make_token(role="user", user_id="user-uid")
        user = _mock_user(role="user", user_id="user-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.delete("/auth/users/other-uid",
                                headers=_auth_headers(token))
        assert res.status_code == 403

    def test_delete_superadmin_puo_altri_gruppi(self):
        token = _make_token(role="superadmin", user_id="super-uid", group_id="grp1")
        superadmin = _mock_user(role="superadmin", user_id="super-uid", group_id="grp1")
        target = _mock_user(user_id="target-uid", username="other", group_id="altro-grp")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: superadmin if uid == "super-uid" else target
            mock_db.delete_user.return_value = True
            res = client.delete("/auth/users/target-uid",
                                headers=_auth_headers(token))
        assert res.status_code == 204
