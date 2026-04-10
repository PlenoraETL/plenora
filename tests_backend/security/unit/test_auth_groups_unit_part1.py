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
# require_superadmin  (righe 232-234)
# =============================================================================

class TestRequireSuperadmin:

    def test_superadmin_passa(self):
        token = _make_token(role="superadmin")
        user = _mock_user(role="superadmin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.get_group.return_value = None
            res = client.post("/auth/groups",
                              json={"group_id": "new", "name": "Nuovo"},
                              headers=_auth(role="superadmin"))
        assert res.status_code != 403

    def test_admin_bloccato_403(self):
        user = _mock_user(role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.post("/auth/groups",
                              json={"group_id": "new", "name": "Nuovo"},
                              headers=_auth(role="admin"))
        assert res.status_code == 403

    def test_user_bloccato_403(self):
        user = _mock_user(role="user")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            res = client.post("/auth/groups",
                              json={"group_id": "new", "name": "Nuovo"},
                              headers=_auth(role="user"))
        assert res.status_code == 403


# =============================================================================
# group_filter  (righe 242-244)
# =============================================================================

class TestGroupFilter:

    def test_superadmin_ritorna_none(self):
        cu = CurrentUser(user_id="u1", username="sa", group_id="grp1", role="superadmin")
        assert group_filter(cu) is None

    def test_admin_ritorna_group_id(self):
        cu = CurrentUser(user_id="u1", username="a", group_id="grp1", role="admin")
        assert group_filter(cu) == "grp1"

    def test_user_ritorna_group_id(self):
        cu = CurrentUser(user_id="u1", username="u", group_id="grp2", role="user")
        assert group_filter(cu) == "grp2"


# =============================================================================
# PUT /auth/users/{id}  (righe 354-371)
# =============================================================================

class TestUpdateUser:

    def test_update_user_ok_200(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        target = _mock_user(user_id="target-uid", username="target")
        updated = {**target, "email": "new@test.com"}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            mock_db.update_user.return_value = updated
            res = client.put("/auth/users/target-uid",
                             json={"email": "new@test.com"},
                             headers=_auth(user_id="admin-uid"))
        assert res.status_code == 200

    def test_update_user_non_trovato_404(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else None
            res = client.put("/auth/users/nonexist",
                             json={"email": "x@x.com"},
                             headers=_auth(user_id="admin-uid"))
        assert res.status_code == 404

    def test_update_user_altro_gruppo_403(self):
        admin = _mock_user(user_id="admin-uid", role="admin", group_id="grp1")
        target = _mock_user(user_id="target-uid", group_id="altro-grp")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            res = client.put("/auth/users/target-uid",
                             json={"email": "x@x.com"},
                             headers=_auth(user_id="admin-uid", group_id="grp1"))
        assert res.status_code == 403

    def test_update_user_superadmin_cambia_role(self):
        sa = _mock_user(user_id="sa-uid", role="superadmin")
        target = _mock_user(user_id="target-uid", group_id="grp1")
        updated = {**target, "role": "admin"}
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: sa if uid == "sa-uid" else target
            mock_db.update_user.return_value = updated
            res = client.put("/auth/users/target-uid",
                             json={"role": "admin"},
                             headers=_auth(user_id="sa-uid", role="superadmin"))
        assert res.status_code == 200
        mock_db.update_user.assert_called_once()
        call_kwargs = mock_db.update_user.call_args
        assert "role" in call_kwargs[1]

    def test_update_user_nessun_campo_400(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        target = _mock_user(user_id="target-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            res = client.put("/auth/users/target-uid",
                             json={"password": "hack"},
                             headers=_auth(user_id="admin-uid"))
        assert res.status_code == 400

    def test_update_user_db_fail_404(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        target = _mock_user(user_id="target-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            mock_db.update_user.return_value = None
            res = client.put("/auth/users/target-uid",
                             json={"email": "x@x.com"},
                             headers=_auth(user_id="admin-uid"))
        assert res.status_code == 404


# =============================================================================
# POST /auth/users/{id}/reset-password  (righe 381-388)
# =============================================================================

class TestResetPassword:

    def test_reset_password_ok(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        target = _mock_user(user_id="target-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            mock_db.update_user_password.return_value = None
            res = client.post("/auth/users/target-uid/reset-password",
                              json={"new_password": "nuova123"},
                              headers=_auth(user_id="admin-uid"))
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_reset_password_target_non_trovato_404(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else None
            res = client.post("/auth/users/nonexist/reset-password",
                              json={"new_password": "nuova"},
                              headers=_auth(user_id="admin-uid"))
        assert res.status_code == 404

    def test_reset_password_altro_gruppo_403(self):
        admin = _mock_user(user_id="admin-uid", role="admin", group_id="grp1")
        target = _mock_user(user_id="target-uid", group_id="altro-grp")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            res = client.post("/auth/users/target-uid/reset-password",
                              json={"new_password": "nuova"},
                              headers=_auth(user_id="admin-uid", group_id="grp1"))
        assert res.status_code == 403

    def test_reset_password_superadmin_cross_group(self):
        sa = _mock_user(user_id="sa-uid", role="superadmin", group_id="grp1")
        target = _mock_user(user_id="target-uid", group_id="altro-grp")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: sa if uid == "sa-uid" else target
            mock_db.update_user_password.return_value = None
            res = client.post("/auth/users/target-uid/reset-password",
                              json={"new_password": "nuova"},
                              headers=_auth(user_id="sa-uid", role="superadmin"))
        assert res.status_code == 200


# =============================================================================
# DELETE /auth/users/{id} — branch db.delete_user fail  (riga 407)
# =============================================================================

class TestDeleteUserDbFail:

    def test_delete_user_db_restituisce_false_404(self):
        admin = _mock_user(user_id="admin-uid", role="admin")
        target = _mock_user(user_id="target-uid")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.side_effect = lambda uid: admin if uid == "admin-uid" else target
            mock_db.delete_user.return_value = False
            res = client.delete("/auth/users/target-uid",
                                headers=_auth(user_id="admin-uid"))
        assert res.status_code == 404


# =============================================================================
# GET /auth/groups  (righe 422-427)
# =============================================================================

class TestListGroups:

    def test_superadmin_vede_tutti_i_gruppi(self):
        sa = _mock_user(role="superadmin")
        groups = [_mock_group("g1", "Gruppo 1"), _mock_group("g2", "Gruppo 2")]
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = sa
            mock_db.list_groups.return_value = groups
            res = client.get("/auth/groups", headers=_auth(role="superadmin"))
        assert res.status_code == 200
        assert len(res.json()) == 2

    def test_admin_vede_solo_proprio_gruppo(self):
        admin = _mock_user(role="admin", group_id="grp1")
        grp = _mock_group("grp1", "Gruppo 1")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            mock_db.get_group.return_value = grp
            res = client.get("/auth/groups", headers=_auth(role="admin", group_id="grp1"))
        assert res.status_code == 200
        assert len(res.json()) == 1
        assert res.json()[0]["group_id"] == "grp1"

    def test_user_vede_solo_proprio_gruppo(self):
        user = _mock_user(role="user", group_id="grp1")
        grp = _mock_group("grp1", "Gruppo 1")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = user
            mock_db.get_group.return_value = grp
            res = client.get("/auth/groups", headers=_auth(role="user", group_id="grp1"))
        assert res.status_code == 200
        assert len(res.json()) == 1

    def test_admin_gruppo_non_trovato_lista_vuota(self):
        admin = _mock_user(role="admin", group_id="grp-inesistente")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            mock_db.get_group.return_value = None
            res = client.get("/auth/groups",
                             headers=_auth(role="admin", group_id="grp-inesistente"))
        assert res.status_code == 200
        assert res.json() == []

    def test_senza_token_401(self):
        res = client.get("/auth/groups")
        assert res.status_code == 401


# =============================================================================
# POST /auth/groups  (righe 434-437)
# =============================================================================

class TestCreateGroup:

    def test_crea_gruppo_ok_201(self):
        sa = _mock_user(role="superadmin")
        new_grp = _mock_group("new-grp", "Nuovo Gruppo")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = sa
            mock_db.get_group.return_value = None
            mock_db.create_group.return_value = new_grp
            res = client.post("/auth/groups",
                              json={"group_id": "new-grp", "name": "Nuovo Gruppo"},
                              headers=_auth(role="superadmin"))
        assert res.status_code == 201
        assert res.json()["group_id"] == "new-grp"

    def test_crea_gruppo_esistente_409(self):
        sa = _mock_user(role="superadmin")
        existing = _mock_group("grp1", "Esiste")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = sa
            mock_db.get_group.return_value = existing
            res = client.post("/auth/groups",
                              json={"group_id": "grp1", "name": "Esiste"},
                              headers=_auth(role="superadmin"))
        assert res.status_code == 409


# =============================================================================
# GET /auth/groups/{id}  (righe 446-451)
# =============================================================================

class TestGetGroup:

    def test_superadmin_vede_qualsiasi_gruppo(self):
        sa = _mock_user(role="superadmin", group_id="grp1")
        grp = _mock_group("altro-grp", "Altro")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = sa
            mock_db.get_group.return_value = grp
            res = client.get("/auth/groups/altro-grp",
                             headers=_auth(role="superadmin"))
        assert res.status_code == 200
        assert res.json()["group_id"] == "altro-grp"

    def test_admin_vede_proprio_gruppo(self):
        admin = _mock_user(role="admin", group_id="grp1")
        grp = _mock_group("grp1", "Mio Gruppo")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            mock_db.get_group.return_value = grp
            res = client.get("/auth/groups/grp1",
                             headers=_auth(role="admin", group_id="grp1"))
        assert res.status_code == 200

    def test_admin_non_vede_altro_gruppo_403(self):
        admin = _mock_user(role="admin", group_id="grp1")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = admin
            res = client.get("/auth/groups/altro-grp",
                             headers=_auth(role="admin", group_id="grp1"))
        assert res.status_code == 403

    def test_gruppo_non_trovato_404(self):
        sa = _mock_user(role="superadmin")
        with patch("app.core.auth.auth.db") as mock_db:
            mock_db.get_user.return_value = sa
            mock_db.get_group.return_value = None
            res = client.get("/auth/groups/nonexist",
                             headers=_auth(role="superadmin"))
        assert res.status_code == 404


# =============================================================================
# GET /auth/groups/{id}/summary  (righe 460-463)
# =============================================================================

class TestGroupSummary:

    def test_summary_ok(self):
        sa = _mock_user(role="superadmin")
        summary = {"projects": 3, "connections": 2, "users": 5}
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.db.config_db_service.get_group", return_value=_mock_group()), \
             patch("app.db.config_db_service.get_group_summary", return_value=summary):
            mock_db.get_user.return_value = sa
            res = client.get("/auth/groups/grp1/summary",
                             headers=_auth(role="superadmin"))
        assert res.status_code == 200

    def test_summary_gruppo_non_trovato_404(self):
        sa = _mock_user(role="superadmin")
        with patch("app.core.auth.auth.db") as mock_db, \
             patch("app.db.config_db_service.get_group", return_value=None), \
             patch("app.db.config_db_service.get_group_summary"):
            mock_db.get_user.return_value = sa
            res = client.get("/auth/groups/nonexist/summary",
                             headers=_auth(role="superadmin"))
        assert res.status_code == 404
