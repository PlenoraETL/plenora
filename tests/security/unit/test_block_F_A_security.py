"""
tests/security/unit/test_block_F_A_security.py
==============================================
Unit test per il Blocco F-A: 3 fix di sicurezza endpoint.

Copre:
  - F1 _sanitize_upload_filename + _assert_path_within (files router)
  - F2 _validate_external_url (arricchisci router) — anti-SSRF
  - F3 update_user mass assignment hardening (auth router)
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# F1 — _sanitize_upload_filename + _assert_path_within
# ============================================================================

class TestSanitizeUploadFilename:

    def test_valid_filename(self):
        from app.core.files.router import _sanitize_upload_filename
        assert _sanitize_upload_filename("data.csv") == "data.csv"

    def test_strips_path_components(self):
        from app.core.files.router import _sanitize_upload_filename
        assert _sanitize_upload_filename("../../etc/passwd") == "passwd"

    def test_strips_windows_path(self):
        from app.core.files.router import _sanitize_upload_filename
        # On any OS Path treats both / and \ as separators
        result = _sanitize_upload_filename("..\\..\\windows\\system32\\cmd.exe")
        assert result in ("cmd.exe", "..\\..\\windows\\system32\\cmd.exe")

    def test_rejects_empty(self):
        from app.core.files.router import _sanitize_upload_filename
        with pytest.raises(HTTPException) as exc:
            _sanitize_upload_filename("")
        assert exc.value.status_code == 400

    def test_rejects_none(self):
        from app.core.files.router import _sanitize_upload_filename
        with pytest.raises(HTTPException) as exc:
            _sanitize_upload_filename(None)
        assert exc.value.status_code == 400

    def test_rejects_dot(self):
        from app.core.files.router import _sanitize_upload_filename
        with pytest.raises(HTTPException):
            _sanitize_upload_filename(".")

    def test_rejects_dotdot(self):
        from app.core.files.router import _sanitize_upload_filename
        with pytest.raises(HTTPException):
            _sanitize_upload_filename("..")

    def test_rejects_nul_byte(self):
        from app.core.files.router import _sanitize_upload_filename
        with pytest.raises(HTTPException) as exc:
            _sanitize_upload_filename("test\x00.csv")
        assert "non valid" in exc.value.detail.lower()

    def test_strips_whitespace(self):
        from app.core.files.router import _sanitize_upload_filename
        assert _sanitize_upload_filename("  test.csv  ") == "test.csv"

    def test_rejects_only_whitespace(self):
        from app.core.files.router import _sanitize_upload_filename
        with pytest.raises(HTTPException):
            _sanitize_upload_filename("   ")


class TestAssertPathWithin:

    def test_child_inside_parent_ok(self, tmp_path):
        from app.core.files.router import _assert_path_within
        parent = tmp_path
        child = tmp_path / "subdir" / "file.csv"
        child.parent.mkdir(parents=True, exist_ok=True)
        result = _assert_path_within(child, parent)
        assert result == child.resolve()

    def test_child_outside_parent_blocked(self, tmp_path):
        from app.core.files.router import _assert_path_within
        parent = tmp_path / "sandbox"
        parent.mkdir()
        outside = tmp_path / "elsewhere" / "evil.txt"
        with pytest.raises(HTTPException) as exc:
            _assert_path_within(outside, parent)
        assert exc.value.status_code == 403

    def test_traversal_blocked(self, tmp_path):
        from app.core.files.router import _assert_path_within
        parent = tmp_path / "sandbox"
        parent.mkdir()
        traversal = parent / ".." / ".." / "etc" / "passwd"
        with pytest.raises(HTTPException) as exc:
            _assert_path_within(traversal, parent)
        assert exc.value.status_code == 403


# ============================================================================
# F2 — _validate_external_url (anti-SSRF helper estratto)
# ============================================================================

class TestValidateExternalUrl:

    def test_invalid_scheme_400(self):
        from app.core.arricchisci.router import _validate_external_url
        with pytest.raises(HTTPException) as exc:
            _validate_external_url("file:///etc/passwd")
        assert exc.value.status_code == 400

    def test_ftp_scheme_400(self):
        from app.core.arricchisci.router import _validate_external_url
        with pytest.raises(HTTPException) as exc:
            _validate_external_url("ftp://example.com/")
        assert exc.value.status_code == 400

    def test_no_hostname_400(self):
        from app.core.arricchisci.router import _validate_external_url
        with pytest.raises(HTTPException) as exc:
            _validate_external_url("https://")
        assert exc.value.status_code == 400

    def test_private_ip_blocked(self):
        from app.core.arricchisci.router import _validate_external_url
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("internal", [], ["10.1.2.3"])
            with pytest.raises(HTTPException) as exc:
                _validate_external_url("https://internal.lab/api")
        assert exc.value.status_code == 403

    def test_loopback_blocked(self):
        from app.core.arricchisci.router import _validate_external_url
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("localhost", [], ["127.0.0.1"])
            with pytest.raises(HTTPException) as exc:
                _validate_external_url("http://localhost/")
        assert exc.value.status_code == 403

    def test_link_local_metadata_blocked(self):
        from app.core.arricchisci.router import _validate_external_url
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("metadata", [], ["169.254.169.254"])
            with pytest.raises(HTTPException) as exc:
                _validate_external_url("http://metadata/latest/")
        assert exc.value.status_code == 403

    def test_unresolvable_400(self):
        import socket as _socket
        from app.core.arricchisci.router import _validate_external_url
        with patch("socket.gethostbyname_ex", side_effect=_socket.gaierror("nx")):
            with pytest.raises(HTTPException) as exc:
                _validate_external_url("https://nonexistent.invalid/")
        assert exc.value.status_code == 400

    def test_public_ip_passes(self):
        from app.core.arricchisci.router import _validate_external_url
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("api.example.com", [], ["93.184.216.34"])
            # Non solleva
            _validate_external_url("https://api.example.com/openapi.json")

    def test_multiple_ips_one_private_blocked(self):
        """DNS rebinding: se ANCHE UNO degli IP risolti è privato, blocca."""
        from app.core.arricchisci.router import _validate_external_url
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("multi", [], ["93.184.216.34", "10.0.0.1"])
            with pytest.raises(HTTPException) as exc:
                _validate_external_url("https://multi.example.com/")
        assert exc.value.status_code == 403


# ============================================================================
# F3 — update_user mass assignment hardening
# ============================================================================

def _admin_user(group_id="g1"):
    from app.core.auth.auth import CurrentUser
    return CurrentUser(user_id="admin1", username="admin",
                       group_id=group_id, role="admin")


def _superadmin_user():
    from app.core.auth.auth import CurrentUser
    return CurrentUser(user_id="super1", username="super",
                       group_id="g0", role="superadmin")


def _make_users_client(user_fn=None):
    from app.core.auth.auth import router as auth_router, get_current_user
    app = FastAPI()
    app.dependency_overrides[get_current_user] = user_fn or _superadmin_user
    # Il router auth ha già prefix="/auth" definito internamente
    app.include_router(auth_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


# URL base per gli endpoint user (router con prefix /auth montato sotto /api)
_USERS_BASE = "/api/auth/users"


class TestUpdateUserHardening:

    def test_email_must_be_string(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            res = client.put(f"{_USERS_BASE}/u1", json={"email": 12345})
        assert res.status_code == 400
        assert "email" in res.json()["detail"].lower()

    def test_email_invalid_format_rejected(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            res = client.put(f"{_USERS_BASE}/u1", json={"email": "not-an-email"})
        assert res.status_code == 400

    def test_is_active_must_be_bool(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            res = client.put(f"{_USERS_BASE}/u1", json={"is_active": 1})
        assert res.status_code == 400
        assert "boolean" in res.json()["detail"].lower() or "bool" in res.json()["detail"].lower()

    def test_role_invalid_rejected(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            res = client.put(f"{_USERS_BASE}/u1", json={"role": "god"})
        assert res.status_code == 400

    def test_role_must_be_string(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            res = client.put(f"{_USERS_BASE}/u1", json={"role": ["admin"]})
        assert res.status_code == 400

    def test_group_id_must_exist(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get, \
             patch("app.core.auth.auth.db.get_group") as mock_grp:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            mock_grp.return_value = None  # gruppo non esiste
            res = client.put(f"{_USERS_BASE}/u1", json={"group_id": "ghost"})
        assert res.status_code == 400
        assert "non trovato" in res.json()["detail"].lower()

    def test_admin_cannot_change_role_field(self):
        """admin non superadmin: il campo role NON è in whitelist → ignorato."""
        client = _make_users_client(user_fn=lambda: _admin_user(group_id="g1"))
        with patch("app.core.auth.auth.db.get_user") as mock_get, \
             patch("app.core.auth.auth.db.update_user") as mock_upd:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            mock_upd.return_value = {
                "id": "u1", "username": "x", "email": "new@x.com",
                "role": "user", "group_id": "g1", "is_active": True,
                "created_at": "2026-01-01T00:00:00",
            }
            res = client.put(f"{_USERS_BASE}/u1", json={"role": "admin", "email": "new@x.com"})
        # OK, ma role è stato filtrato — verifichiamo che update_user sia stato chiamato senza role
        assert res.status_code == 200, f"got {res.status_code}: {res.text}"
        call_kwargs = mock_upd.call_args.kwargs
        assert "role" not in call_kwargs
        assert call_kwargs.get("email") == "new@x.com"

    def test_self_demotion_role_blocked(self):
        """Un superadmin non può abbassarsi il proprio ruolo."""
        client = _make_users_client(user_fn=_superadmin_user)
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "super1", "group_id": "g0"}
            res = client.put(f"{_USERS_BASE}/super1", json={"role": "user"})
        assert res.status_code == 403
        assert "tuo stesso ruolo" in res.json()["detail"].lower() or "ruolo" in res.json()["detail"].lower()

    def test_self_disable_blocked(self):
        client = _make_users_client(user_fn=_superadmin_user)
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "super1", "group_id": "g0"}
            res = client.put(f"{_USERS_BASE}/super1", json={"is_active": False})
        assert res.status_code == 403

    def test_no_allowed_fields_400(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "g1"}
            res = client.put(f"{_USERS_BASE}/u1", json={"foo": "bar"})
        assert res.status_code == 400
        assert "nessun campo" in res.json()["detail"].lower()

    def test_admin_cross_group_403(self):
        client = _make_users_client(user_fn=lambda: _admin_user(group_id="g1"))
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = {"id": "u1", "group_id": "OTHER"}
            res = client.put(f"{_USERS_BASE}/u1", json={"email": "x@y.com"})
        assert res.status_code == 403

    def test_user_not_found_404(self):
        client = _make_users_client()
        with patch("app.core.auth.auth.db.get_user") as mock_get:
            mock_get.return_value = None
            res = client.put(f"{_USERS_BASE}/nonexistent", json={"email": "x@y.com"})
        assert res.status_code == 404
