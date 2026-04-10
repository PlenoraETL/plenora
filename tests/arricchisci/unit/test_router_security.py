"""
tests/arricchisci/unit/test_router_security.py
==============================================
Unit tests per i fix di sicurezza in app.core.arricchisci.router:

  - _validate_sandboxed_path (sandbox in DATA_DIR)
  - POST /executables/test (require_admin, sandbox path, env filter)
  - POST /api-connections/fetch-openapi-schema (anti-SSRF + verify SSL)
"""

import sys
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


# Override del fixture autouse `mock_reset` (root conftest.py) che fa skip
# se il mock_api_server non gira. Questi test usano TestClient, niente network.
@pytest.fixture(autouse=True)
def mock_reset():
    yield


import app.core.arricchisci.router as R
from app.core.auth.auth import get_current_user, CurrentUser


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _admin_user():
    return CurrentUser(
        user_id="u-admin", username="admin",
        group_id="g1", role="admin",
    )


def _viewer_user():
    return CurrentUser(
        user_id="u-viewer", username="viewer",
        group_id="g1", role="viewer",
    )


def make_client(user_fn=None):
    app = FastAPI()
    app.dependency_overrides[get_current_user] = user_fn or _admin_user
    app.include_router(R.api_connections_router)
    app.include_router(R.executables_router)
    return TestClient(app, raise_server_exceptions=False)


# ============================================================================
# _validate_sandboxed_path
# ============================================================================

class TestValidateSandboxedPath:

    def test_path_inside_data_dir_ok(self, tmp_path):
        # Setup: crea un file dentro tmp_path (simulando DATA_DIR)
        f = tmp_path / "executables" / "myscript.py"
        f.parent.mkdir(parents=True)
        f.write_text("print('hi')")
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            resolved = R._validate_sandboxed_path(str(f), "Eseguibile")
        assert resolved == f.resolve()

    def test_path_outside_data_dir_403(self, tmp_path):
        # Path fuori da DATA_DIR (es. /etc/passwd)
        outside = tmp_path / ".." / "outside" / "x.sh"
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            with pytest.raises(HTTPException) as exc:
                R._validate_sandboxed_path(str(outside), "Eseguibile")
        assert exc.value.status_code == 403
        assert "sandbox" in exc.value.detail.lower()

    def test_path_traversal_blocked(self, tmp_path):
        # Path con .. che cerca di uscire da DATA_DIR
        traversal = str(tmp_path / "subdir" / ".." / ".." / "etc" / "passwd")
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            with pytest.raises(HTTPException) as exc:
                R._validate_sandboxed_path(traversal, "Eseguibile")
        assert exc.value.status_code == 403

    def test_empty_path_400(self, tmp_path):
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            with pytest.raises(HTTPException) as exc:
                R._validate_sandboxed_path("", "Eseguibile")
        assert exc.value.status_code == 400

    def test_absolute_system_path_blocked(self, tmp_path):
        # /etc/passwd è chiaramente fuori da DATA_DIR
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            with pytest.raises(HTTPException) as exc:
                R._validate_sandboxed_path("/etc/passwd", "Eseguibile")
        assert exc.value.status_code == 403


# ============================================================================
# POST /executables/test — auth + sandbox + env filter
# ============================================================================

class TestExecutablesTestEndpoint:

    def test_non_admin_forbidden(self):
        """Viewer/user non possono testare eseguibili (RCE potential)."""
        client = make_client(user_fn=_viewer_user)
        res = client.post("/executables/test", json={
            "executable": {"type": "python", "path": "/anything"},
            "test_input": "{}"
        })
        # require_admin → 403
        assert res.status_code == 403

    def test_admin_with_path_outside_sandbox_403(self, tmp_path):
        """Admin che prova a eseguire /bin/bash → 403 sandbox."""
        client = make_client()
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            res = client.post("/executables/test", json={
                "executable": {"type": "shell", "path": "/bin/bash"},
                "test_input": "{}"
            })
        assert res.status_code == 403

    def test_admin_with_nonexistent_path_in_sandbox(self, tmp_path):
        """Admin con path nella sandbox ma file non esistente → success=False."""
        client = make_client()
        nonexistent = tmp_path / "missing.py"
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            res = client.post("/executables/test", json={
                "executable": {"type": "python", "path": str(nonexistent)},
                "test_input": "{}"
            })
        # 200 ma success=False ("Eseguibile non trovato")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "non trovato" in body["error"].lower()

    def test_builtin_module_test_works_for_admin(self):
        """Test built-in module (importlib) — non passa per subprocess."""
        # Aggiornato dopo G-2/N3: solo moduli in BUILTIN_REGISTRY ammessi.
        # Mock di BUILTIN_REGISTRY per usare un modulo std (json) come whitelist.
        client = make_client()
        with patch("app.core.arricchisci.executable_runner.BUILTIN_REGISTRY",
                   {"json_test": {"module": "json"}}):
            res = client.post("/executables/test", json={
                "executable": {
                    "module": "json",
                    "function": "loads",
                },
                "test_input": "{}"
            })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True

    def test_builtin_module_missing_function(self):
        client = make_client()
        with patch("app.core.arricchisci.executable_runner.BUILTIN_REGISTRY",
                   {"json_test": {"module": "json"}}):
            res = client.post("/executables/test", json={
                "executable": {
                    "module": "json",
                    "function": "nonexistent_xyz",
                },
                "test_input": "{}"
            })
        assert res.status_code == 200
        assert res.json()["success"] is False

    def test_builtin_module_not_in_whitelist_blocked(self):
        """G-2/N3: moduli non in BUILTIN_REGISTRY vengono rifiutati."""
        client = make_client()
        res = client.post("/executables/test", json={
            "executable": {
                "module": "os",
                "function": "system",
            },
            "test_input": "{}"
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "whitelist" in body["error"].lower()

    def test_blocked_env_vars_filtered(self, tmp_path):
        """LD_PRELOAD/PYTHONPATH passati dall'utente vengono filtrati."""
        # Crea un piccolo script Python che stampa l'env
        script = tmp_path / "echo_env.py"
        script.write_text(
            "import os; print('LD_PRELOAD=' + os.environ.get('LD_PRELOAD','MISSING'))"
        )
        client = make_client()
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = b"LD_PRELOAD=MISSING"
                mock_proc.stderr = b""
                mock_run.return_value = mock_proc
                res = client.post("/executables/test", json={
                    "executable": {
                        "type": "executable",
                        "path": str(script),
                        "env_vars": {"LD_PRELOAD": "/tmp/evil.so", "MY_OK": "yes"},
                    },
                    "test_input": "{}"
                })
        assert res.status_code == 200
        # Verifica che subprocess.run sia stato chiamato e che l'env passato
        # non contenga LD_PRELOAD ma contenga MY_OK
        call_args = mock_run.call_args
        env_passed = call_args.kwargs.get("env") or call_args[1].get("env")
        assert env_passed is not None
        assert "LD_PRELOAD" not in env_passed
        assert env_passed.get("MY_OK") == "yes"

    def test_timeout_capped(self, tmp_path):
        """Timeout > 60s viene capped a 60s."""
        script = tmp_path / "x.py"
        script.write_text("print('ok')")
        client = make_client()
        with patch("app.core.arricchisci.router.settings") as mock_settings:
            mock_settings.DATA_DIR = tmp_path
            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = b"ok"
                mock_proc.stderr = b""
                mock_run.return_value = mock_proc
                client.post("/executables/test", json={
                    "executable": {
                        "type": "python",
                        "path": str(script),
                        "timeout": 9999,  # tentativo di bypassare il cap
                    },
                    "test_input": "{}"
                })
        timeout_passed = mock_run.call_args.kwargs.get("timeout")
        assert timeout_passed == 60


# ============================================================================
# POST /api-connections/fetch-openapi-schema — anti-SSRF + SSL verify
# ============================================================================

class TestFetchOpenapiSchemaSSRF:

    def test_invalid_scheme_400(self):
        client = make_client()
        res = client.post("/api-connections/fetch-openapi-schema", json={
            "url": "file:///etc/passwd"
        })
        assert res.status_code == 400
        assert "http" in res.json()["detail"].lower()

    def test_missing_hostname_400(self):
        client = make_client()
        res = client.post("/api-connections/fetch-openapi-schema", json={
            "url": "https://"
        })
        # Pydantic potrebbe accettare la stringa, dovrebbe fallire al parsing dell'host
        assert res.status_code in (400, 422)

    def test_private_ip_blocked(self):
        """URL che risolve a IP privato (10/8) → 403."""
        client = make_client()
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("internal.lab", [], ["10.0.0.5"])
            res = client.post("/api-connections/fetch-openapi-schema", json={
                "url": "https://internal.lab/openapi.json"
            })
        assert res.status_code == 403
        assert "privato" in res.json()["detail"].lower() or "interno" in res.json()["detail"].lower()

    def test_loopback_blocked(self):
        client = make_client()
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("localhost", [], ["127.0.0.1"])
            res = client.post("/api-connections/fetch-openapi-schema", json={
                "url": "http://localhost:6379/"
            })
        assert res.status_code == 403

    def test_link_local_metadata_blocked(self):
        """169.254.169.254 (EC2/cloud metadata) → 403."""
        client = make_client()
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("metadata.aws", [], ["169.254.169.254"])
            res = client.post("/api-connections/fetch-openapi-schema", json={
                "url": "http://metadata.aws/latest/meta-data/"
            })
        assert res.status_code == 403

    def test_unresolvable_host_400(self):
        import socket as _socket
        client = make_client()
        with patch("socket.gethostbyname_ex", side_effect=_socket.gaierror("not found")):
            res = client.post("/api-connections/fetch-openapi-schema", json={
                "url": "https://nonexistent.invalid/"
            })
        assert res.status_code == 400
        assert "risolvibile" in res.json()["detail"].lower() or "non risolv" in res.json()["detail"].lower()

    def test_public_ip_passes_validation_then_uses_httpx(self):
        """Public IP supera la validation → procede a httpx (mockato)."""
        client = make_client()
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("api.example.com", [], ["93.184.216.34"])
            with patch("httpx.AsyncClient") as mock_client_cls:
                # Setup async context manager mock
                mock_client = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"openapi": "3.0.0"}
                mock_resp.raise_for_status = MagicMock()

                async def _aget(*a, **kw):
                    return mock_resp

                mock_client.get = _aget

                async def _aenter(_self):
                    return mock_client

                async def _aexit(_self, *a):
                    return None

                instance = MagicMock()
                instance.__aenter__ = _aenter
                instance.__aexit__ = _aexit
                mock_client_cls.return_value = instance

                res = client.post("/api-connections/fetch-openapi-schema", json={
                    "url": "https://api.example.com/openapi.json"
                })
        # Il test verifica che la validazione passi; potrebbe arrivare a 200 o
        # 502 se il mock non è perfetto. L'importante è che NON sia 400/403.
        assert res.status_code not in (400, 403)

    def test_verify_ssl_is_true_by_default(self):
        """Il fix forza verify=True (no MITM via cert invalidi)."""
        client = make_client()
        with patch("socket.gethostbyname_ex") as mock_dns:
            mock_dns.return_value = ("api.example.com", [], ["93.184.216.34"])
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.side_effect = RuntimeError("stop here")
                client.post("/api-connections/fetch-openapi-schema", json={
                    "url": "https://api.example.com/openapi.json"
                })
        # Verifica che httpx.AsyncClient sia stato chiamato SENZA verify=False
        # (cioè con verify=True, default)
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs.get("verify", True) is not False
