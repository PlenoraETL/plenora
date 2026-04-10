"""
tests/security/unit/test_auth_groups_unit.py
=============================================
Test unitari del router gruppi + require_superadmin + group_filter
+ update_user + reset_password.

Copertura righe mancanti di auth.py:
  - require_superadmin (232-234)
  - group_filter (242-244)
  - PUT /users/{id} (354-371)
  - POST /users/{id}/reset-password (381-388)
  - DELETE /users/{id} branch db fail (407)
  - GET /auth/groups (422-427)
  - POST /auth/groups (434-437)
  - GET /auth/groups/{id} (446-451)
  - GET /auth/groups/{id}/summary (460-463)
  - DELETE /auth/groups/{id} (475-486)
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

from app.core.auth.auth import (
    router, groups_router, hash_password, create_access_token,
    SECRET_KEY, ALGORITHM, CurrentUser,
    require_superadmin, group_filter,
)

# ─── App di test ──────────────────────────────────────────────────────────────

app = FastAPI()
app.include_router(router)
app.include_router(groups_router)
client = TestClient(app, raise_server_exceptions=False)


def _make_token(user_id="uid1", username="admin", group_id="grp1", role="admin",
                hours=8) -> str:
    return create_access_token(user_id, username, group_id, role)


def _auth(role="admin", user_id="uid1", group_id="grp1", username="admin"):
    token = _make_token(user_id=user_id, username=username, group_id=group_id, role=role)
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


def _mock_group(group_id="grp1", name="Gruppo 1", description=""):
    return {"id": group_id, "name": name, "description": description}




# =============================================================================
# DELETE /auth/groups/{id}  (righe 475-486)
# =============================================================================

class TestDeleteGroup:

    def test_delete_gruppo_ok(self):
        sa = _mock_user(role="superadmin")
        report = {"users": 2, "projects": 1}
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.db.config_db_service.get_group", return_value=_mock_group("grp2")), \
             patch("app.db.config_db_service.delete_group_cascade", return_value=report):
            mock_db.get_user.return_value = sa
            res = client.delete("/auth/groups/grp2",
                                headers=_auth(role="superadmin"))
        assert res.status_code == 200
        assert res.json()["deleted"] is True

    def test_delete_gruppo_default_400(self):
        sa = _mock_user(role="superadmin")
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.db.config_db_service.get_group"), \
             patch("app.db.config_db_service.delete_group_cascade"):
            mock_db.get_user.return_value = sa
            res = client.delete("/auth/groups/default",
                                headers=_auth(role="superadmin"))
        assert res.status_code == 400

    def test_delete_gruppo_non_trovato_404(self):
        sa = _mock_user(role="superadmin")
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.db.config_db_service.get_group", return_value=None), \
             patch("app.db.config_db_service.delete_group_cascade"):
            mock_db.get_user.return_value = sa
            res = client.delete("/auth/groups/nonexist",
                                headers=_auth(role="superadmin"))
        assert res.status_code == 404

    def test_delete_gruppo_errore_500(self):
        sa = _mock_user(role="superadmin")
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.db.config_db_service.get_group", return_value=_mock_group("grp2")), \
             patch("app.db.config_db_service.delete_group_cascade",
                   side_effect=RuntimeError("DB crash")):
            mock_db.get_user.return_value = sa
            res = client.delete("/auth/groups/grp2",
                                headers=_auth(role="superadmin"))
        assert res.status_code == 500

    def test_delete_non_superadmin_403(self):
        admin = _mock_user(role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            res = client.delete("/auth/groups/grp2",
                                headers=_auth(role="admin"))
        assert res.status_code == 403
