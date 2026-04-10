"""
tests/arricchisci/unit/test_router_coverage.py

Unit tests targeting uncovered lines in app.core.arricchisci.router (lines 152-1015).
"""

import json
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

pytestmark = pytest.mark.unit

import app.core.arricchisci.router as R
from app.core.auth.auth import get_current_user, CurrentUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_user():
    return CurrentUser(
        user_id="test-uid", username="test",
        group_id="default", role="superadmin",
    )


def _mock_user_non_admin():
    return CurrentUser(
        user_id="user2", username="viewer",
        group_id="group-b", role="viewer",
    )


def make_client(user_fn=None):
    """Build a TestClient with all sub-routers and mocked services."""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = user_fn or _mock_user
    app.include_router(R.enrichment_router)
    app.include_router(R.api_connections_router)
    app.include_router(R.templates_router)
    app.include_router(R.executables_router)
    app.include_router(R.enrich_router)

    mock_prj = MagicMock()
    mock_prj.list_projects.return_value = []
    mock_api = MagicMock()
    R.init_arricchisci(mock_prj, mock_api)
    return TestClient(app, raise_server_exceptions=False), mock_prj, mock_api




# ---------------------------------------------------------------------------
# delete_api_connection with canvas check (lines 423-457)
# ---------------------------------------------------------------------------

class TestDeleteApiConnectionCanvasCheck:

    def test_delete_in_use_returns_409(self):
        client, prj, api = make_client()
        mock_proj = {
            "project_id": "p1", "name": "Progetto1",
            "project_type": "arricchisci",
        }
        prj.list_projects.return_value = [mock_proj]
        mock_canvas = MagicMock()
        mock_canvas.nodes = [
            {"data": {"api_id": "api_to_delete"}}
        ]
        prj.get_project_canvas.return_value = mock_canvas
        api.delete_api_connection.return_value = True

        res = client.delete("/api-connections/api_to_delete")
        assert res.status_code == 409
        body = res.json()
        assert "in_use_by" in body["detail"]

    def test_delete_not_in_use_succeeds(self):
        client, prj, api = make_client()
        prj.list_projects.return_value = []
        api.delete_api_connection.return_value = True
        res = client.delete("/api-connections/api1")
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"

    def test_delete_not_found_returns_404(self):
        client, prj, api = make_client()
        prj.list_projects.return_value = []
        api.delete_api_connection.return_value = False
        res = client.delete("/api-connections/api1")
        assert res.status_code == 404

    def test_delete_canvas_check_error_continues(self):
        """If checking canvas raises an error, delete should still proceed."""
        client, prj, api = make_client()
        prj.list_projects.side_effect = RuntimeError("DB down")
        api.delete_api_connection.return_value = True
        res = client.delete("/api-connections/api1")
        assert res.status_code == 200

    def test_delete_in_use_pydantic_project(self):
        """Projects as Pydantic-like objects with model_dump."""
        client, prj, api = make_client()
        mock_proj = MagicMock()
        mock_proj.model_dump.return_value = {
            "project_id": "p1", "name": "Progetto1",
            "project_type": "arricchisci",
        }
        prj.list_projects.return_value = MagicMock(projects=[mock_proj])
        mock_canvas = MagicMock()
        node = MagicMock()
        node.model_dump.return_value = {"api_id": "api_del", "data": {}}
        mock_canvas.nodes = [node]
        prj.get_project_canvas.return_value = mock_canvas

        res = client.delete("/api-connections/api_del")
        assert res.status_code == 409

    def test_delete_canvas_none_skips_project(self):
        """If canvas is None for a project, skip it and continue."""
        client, prj, api = make_client()
        prj.list_projects.return_value = [
            {"project_id": "p1", "name": "P1", "project_type": "arricchisci"}
        ]
        prj.get_project_canvas.return_value = None
        api.delete_api_connection.return_value = True
        res = client.delete("/api-connections/api1")
        assert res.status_code == 200

    def test_delete_skips_non_arricchisci_projects(self):
        client, prj, api = make_client()
        prj.list_projects.return_value = [
            {"project_id": "p1", "name": "P1", "project_type": "estrai"}
        ]
        api.delete_api_connection.return_value = True
        res = client.delete("/api-connections/api1")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# test_api_config (POST /api-connections/test) (lines 463-473)
# ---------------------------------------------------------------------------

class TestTestApiConfig:

    def test_test_config_success(self):
        client, _, api = make_client()
        api.test_api_connection.return_value = {
            "success": True, "status_code": 200, "fields": []
        }
        res = client.post("/api-connections/test", json={
            "url": "http://example.com/api",
            "method": "GET",
        })
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_test_config_crash_returns_500(self):
        client, _, api = make_client()
        api.test_api_connection.side_effect = RuntimeError("timeout")
        res = client.post("/api-connections/test", json={
            "url": "http://example.com/api"
        })
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# test_api_connection by ID (POST /api-connections/{id}/test) (lines 482-484)
# ---------------------------------------------------------------------------

class TestTestApiConnectionById:

    def test_test_by_id_crash_returns_500(self):
        client, _, api = make_client()
        api.test_api_connection.side_effect = RuntimeError("fail")
        res = client.post("/api-connections/api1/test", json={})
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# _load_templates / templates (lines 498-550)
# ---------------------------------------------------------------------------

class TestTemplates:

    @patch("app.core.arricchisci.router.TEMPLATES_FILE")
    def test_load_templates_file_not_found(self, mock_path):
        mock_path.exists.return_value = False
        result = R._load_templates()
        assert result == []

    @patch("app.core.arricchisci.router.TEMPLATES_FILE")
    @patch("builtins.open", side_effect=RuntimeError("permission denied"))
    def test_load_templates_read_error(self, _open, mock_path):
        mock_path.exists.return_value = True
        result = R._load_templates()
        assert result == []

    @patch("app.core.arricchisci.router._load_templates")
    def test_get_templates_success(self, mock_lt):
        mock_lt.return_value = [
            {"_meta": {"id": "nominatim", "category": "geocoding"}},
        ]
        client, _, _ = make_client()
        res = client.get("/templates")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["count"] == 1

    @patch("app.core.arricchisci.router._load_templates", side_effect=RuntimeError("boom"))
    def test_get_templates_crash_500(self, _):
        client, _, _ = make_client()
        res = client.get("/templates")
        assert res.status_code == 500

    @patch("app.core.arricchisci.router._load_templates")
    def test_get_categories(self, mock_lt):
        mock_lt.return_value = [
            {"_meta": {"id": "t1", "category": "geocoding"}},
            {"_meta": {"id": "t2", "category": "geocoding"}},
            {"_meta": {"id": "t3", "category": "enrichment"}},
        ]
        client, _, _ = make_client()
        res = client.get("/templates/categories")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        cats = body["categories"]
        assert len(cats) == 2

    @patch("app.core.arricchisci.router._load_templates", side_effect=RuntimeError("boom"))
    def test_get_categories_crash_500(self, _):
        client, _, _ = make_client()
        res = client.get("/templates/categories")
        assert res.status_code == 500

    @patch("app.core.arricchisci.router._load_templates")
    def test_get_template_found(self, mock_lt):
        mock_lt.return_value = [
            {"_meta": {"id": "nominatim", "category": "geocoding"}, "name": "Nominatim"},
        ]
        client, _, _ = make_client()
        res = client.get("/templates/nominatim")
        assert res.status_code == 200
        assert res.json()["success"] is True

    @patch("app.core.arricchisci.router._load_templates")
    def test_get_template_not_found_404(self, mock_lt):
        mock_lt.return_value = []
        client, _, _ = make_client()
        res = client.get("/templates/nonexistent")
        assert res.status_code == 404

    @patch("app.core.arricchisci.router._load_templates", side_effect=RuntimeError("boom"))
    def test_get_template_crash_500(self, _):
        client, _, _ = make_client()
        res = client.get("/templates/x")
        assert res.status_code == 500


# ---------------------------------------------------------------------------
# executables/test (lines 563-656)
# ---------------------------------------------------------------------------

class TestExecutablesTest:

    def test_builtin_module_success(self):
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"module": "json", "function": "loads"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True

    def test_builtin_module_function_not_found(self):
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"module": "json", "function": "nonexistent_fn"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "non trovata" in body["error"]

    def test_builtin_module_import_error(self):
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"module": "nonexistent_module_xyz_123"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "non trovato" in body["error"]

    def test_no_path_returns_error(self):
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"path": ""},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "non configurato" in body["error"]

    @patch("subprocess.run")
    def test_python_script_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b'{"result": "ok"}',
            stderr=b'',
        )
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"type": "python", "path": "/scripts/test.py"},
            "test_input": '{"key": "val"}',
        })
        assert res.status_code == 200
        assert res.json()["success"] is True

    @patch("subprocess.run")
    def test_script_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=b'',
            stderr=b'Error: something went wrong',
        )
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"type": "python", "path": "/scripts/test.py"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "Exit code 1" in body["error"]

    @patch("subprocess.run", side_effect=FileNotFoundError("not found"))
    def test_file_not_found(self, _):
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"type": "executable", "path": "/bin/missing"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "non trovato" in body["error"]

    @patch("subprocess.run")
    def test_timeout_expired(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"type": "python", "path": "/scripts/slow.py", "timeout": 30},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "Timeout" in body["error"]

    @patch("subprocess.run", side_effect=RuntimeError("unexpected"))
    def test_generic_exception(self, _):
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"type": "python", "path": "/scripts/test.py"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False

    @patch("subprocess.run")
    def test_stdin_input_mode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b'ok', stderr=b'',
        )
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {
                "type": "python", "path": "/scripts/test.py",
                "input_mode": "stdin",
            },
            "test_input": '{"x": 1}',
        })
        assert res.status_code == 200
        # Verify stdin was passed
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("input") == b'{"x": 1}'

    @patch("subprocess.run")
    def test_args_input_mode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b'ok', stderr=b'',
        )
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {
                "type": "python", "path": "/scripts/test.py",
                "input_mode": "args",
            },
            "test_input": '{"name": "test"}',
        })
        assert res.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "--name" in cmd
        assert "test" in cmd

    @patch("subprocess.run")
    def test_shell_exec_type(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b'hello', stderr=b'',
        )
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {"type": "shell", "path": "/scripts/run.sh"},
            "test_input": "{}",
        })
        assert res.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "bash"

    @patch("subprocess.run")
    def test_env_vars_passed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b'ok', stderr=b'',
        )
        client, _, _ = make_client()
        res = client.post("/executables/test", json={
            "executable": {
                "type": "python", "path": "/scripts/test.py",
                "env_vars": {"MY_VAR": "123"},
            },
            "test_input": "{}",
        })
        assert res.status_code == 200
        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env is not None
        assert env["MY_VAR"] == "123"


