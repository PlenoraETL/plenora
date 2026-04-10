"""
tests/features/unit/test_features_routes_query.py
==================================================
Unit test per le route FastAPI di query e scrittura del modulo Features.

Copre:
  - POST /features/query/preview     (preview)
  - POST /features/query/execute     (execute)
  - POST /features/write             (write features)
  - POST /features/create-service    (create service)
  - _resolve_input_path helper
"""

import pytest
import logging
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def silence_crash_logs(caplog):
    with caplog.at_level(logging.CRITICAL, logger="app.core.features.routes"):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import app.core.features.routes as routes_mod
from app.core.features.routes import router


@pytest.fixture
def client_and_svc():
    app = FastAPI()
    app.include_router(router)
    mock_svc = MagicMock()
    original = routes_mod.get_service
    routes_mod.get_service = lambda: mock_svc
    yield TestClient(app, raise_server_exceptions=False), mock_svc
    routes_mod.get_service = original


# =============================================================================
# _resolve_input_path
# =============================================================================

class TestResolveInputPath:

    def test_input_path_provided(self):
        from app.core.features.routes import _resolve_input_path
        result = _resolve_input_path("/tmp/data.csv", "")
        assert result == "/tmp/data.csv"

    def test_input_path_takes_priority(self):
        from app.core.features.routes import _resolve_input_path
        result = _resolve_input_path("/explicit/path.csv", "proj-1")
        assert result == "/explicit/path.csv"

    def test_neither_raises(self):
        from app.core.features.routes import _resolve_input_path
        with pytest.raises(ValueError, match="input_path o project_id"):
            _resolve_input_path("", "")

    @patch("app.utils.result_store.get_result_as_tempfile")
    def test_project_id_found(self, mock_get_result):
        from app.core.features.routes import _resolve_input_path
        mock_get_result.return_value = "/tmp/resolved.csv"
        result = _resolve_input_path("", "proj-1")
        assert result == "/tmp/resolved.csv"
        mock_get_result.assert_called_once_with("proj-1")

    @patch("app.utils.result_store.get_result_as_tempfile")
    def test_project_id_not_found(self, mock_get_result):
        from app.core.features.routes import _resolve_input_path
        mock_get_result.return_value = None
        with pytest.raises(FileNotFoundError, match="Nessun output trovato"):
            _resolve_input_path("", "proj-missing")


# =============================================================================
# POST /features/query/preview
# =============================================================================

class TestQueryPreview:

    def test_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.query_layer.return_value = (
            [(1, "Rome", "POINT(12 41)")],
            ["id", "city", "geometry"],
        )
        res = client.post(
            "/features/query/preview",
            params={
                "connection_id": "conn1",
                "layer_url": "https://x/FS/0",
                "where": "1=1",
                "out_fields": "*",
                "max_features": 10,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["rows_count"] == 1
        assert data["columns"] == ["id", "city", "geometry"]

    def test_custom_params(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.query_layer.return_value = ([], [])
        res = client.post(
            "/features/query/preview",
            params={
                "connection_id": "c1",
                "layer_url": "url",
                "where": "STATUS='active'",
                "out_fields": "NAME,STATUS",
                "max_features": 5,
            },
        )
        assert res.status_code == 200
        call_kwargs = mock_svc.query_layer.call_args
        assert call_kwargs.kwargs.get("where") == "STATUS='active'"
        assert call_kwargs.kwargs.get("out_fields") == "NAME,STATUS"
        assert call_kwargs.kwargs.get("max_features") == 5

    def test_missing_params_422(self, client_and_svc):
        client, _ = client_and_svc
        # connection_id and layer_url are required
        res = client.post("/features/query/preview")
        assert res.status_code == 422

    def test_value_error_404(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.query_layer.side_effect = ValueError("non trovata")
        res = client.post(
            "/features/query/preview",
            params={"connection_id": "x", "layer_url": "y"},
        )
        assert res.status_code == 404

    def test_generic_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.query_layer.side_effect = RuntimeError("crash")
        res = client.post(
            "/features/query/preview",
            params={"connection_id": "x", "layer_url": "y"},
        )
        assert res.status_code == 500


# =============================================================================
# POST /features/write
# =============================================================================

class TestWriteFeatures:

    def test_add_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.write_features.return_value = {
            "success": True,
            "message": "Aggiunte 10 features",
            "count": 10,
            "errors": [],
        }
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "input_path": "/tmp/data.csv",
        })
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_delete_with_object_ids(self, client_and_svc):
        """Delete with no input_path/project_id triggers _resolve_input_path ValueError -> 400.
        The route always resolves input path before calling the service, even for delete."""
        client, mock_svc = client_and_svc
        mock_svc.write_features.return_value = {
            "success": True, "message": "Deleted", "count": 3, "errors": [],
        }
        # Must provide input_path or project_id to pass _resolve_input_path
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "delete",
            "input_path": "dummy",
            "object_ids": [1, 2, 3],
        })
        assert res.status_code == 200

    def test_delete_without_paths_returns_400(self, client_and_svc):
        """Delete with no input_path and no project_id raises ValueError in _resolve_input_path."""
        client, mock_svc = client_and_svc
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "delete",
            "object_ids": [1, 2, 3],
        })
        assert res.status_code == 400

    @patch("app.core.features.routes._resolve_input_path")
    def test_resolve_from_project_id(self, mock_resolve, client_and_svc):
        client, mock_svc = client_and_svc
        mock_resolve.return_value = "/tmp/resolved.csv"
        mock_svc.write_features.return_value = {
            "success": True, "message": "ok", "count": 1, "errors": [],
        }
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "project_id": "proj-1",
        })
        assert res.status_code == 200

    def test_value_error_400(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.write_features.side_effect = ValueError("bad param")
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "input_path": "/tmp/f.csv",
        })
        assert res.status_code == 400

    @patch("app.core.features.routes._resolve_input_path")
    def test_file_not_found_404(self, mock_resolve, client_and_svc):
        client, mock_svc = client_and_svc
        mock_resolve.side_effect = FileNotFoundError("not found")
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "project_id": "missing",
        })
        assert res.status_code == 404

    def test_connection_error_502(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.write_features.side_effect = ConnectionError("refused")
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "input_path": "/tmp/f.csv",
        })
        assert res.status_code == 502

    def test_generic_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.write_features.side_effect = RuntimeError("crash")
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "input_path": "/tmp/f.csv",
        })
        assert res.status_code == 500

    def test_missing_body_422(self, client_and_svc):
        client, _ = client_and_svc
        res = client.post("/features/write", json={})
        assert res.status_code == 422

    def test_source_srid_passed(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.write_features.return_value = {
            "success": True, "message": "ok", "count": 1, "errors": [],
        }
        res = client.post("/features/write", json={
            "connection_id": "c1",
            "layer_url": "https://x/FS/0",
            "operation": "add",
            "input_path": "/tmp/f.csv",
            "source_srid": 32632,
        })
        assert res.status_code == 200
        call_kwargs = mock_svc.write_features.call_args.kwargs
        assert call_kwargs.get("source_srid") == 32632


# =============================================================================
# POST /features/create-service
# =============================================================================

class TestCreateService:

    def test_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.create_service.return_value = {
            "success": True,
            "service_url": "https://x/FS",
            "layer_url": "https://x/FS/0",
            "feature_count": 50,
        }
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "NewService",
            "layer_name": "Layer 0",
            "input_path": "/tmp/data.csv",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["feature_count"] == 50

    @patch("app.core.features.routes._resolve_input_path")
    def test_resolve_from_project_id(self, mock_resolve, client_and_svc):
        client, mock_svc = client_and_svc
        mock_resolve.return_value = "/tmp/resolved.csv"
        mock_svc.create_service.return_value = {
            "success": True, "service_url": "x", "layer_url": "y", "feature_count": 1,
        }
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "Svc",
            "project_id": "proj-1",
        })
        assert res.status_code == 200

    def test_value_error_400(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.create_service.side_effect = ValueError("non trovata")
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "Svc",
            "input_path": "/tmp/f.csv",
        })
        assert res.status_code == 400

    @patch("app.core.features.routes._resolve_input_path")
    def test_file_not_found_404(self, mock_resolve, client_and_svc):
        client, mock_svc = client_and_svc
        mock_resolve.side_effect = FileNotFoundError("not found")
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "Svc",
            "project_id": "missing",
        })
        assert res.status_code == 404

    def test_connection_error_502(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.create_service.side_effect = ConnectionError("refused")
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "Svc",
            "input_path": "/tmp/f.csv",
        })
        assert res.status_code == 502

    def test_generic_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.create_service.side_effect = RuntimeError("crash")
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "Svc",
            "input_path": "/tmp/f.csv",
        })
        assert res.status_code == 500

    def test_missing_body_422(self, client_and_svc):
        client, _ = client_and_svc
        res = client.post("/features/create-service", json={})
        assert res.status_code == 422

    def test_source_srid_passed(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.create_service.return_value = {
            "success": True, "service_url": "x", "layer_url": "y", "feature_count": 1,
        }
        res = client.post("/features/create-service", json={
            "connection_id": "c1",
            "service_name": "Svc",
            "input_path": "/tmp/f.csv",
            "source_srid": 4326,
        })
        assert res.status_code == 200
        call_kwargs = mock_svc.create_service.call_args.kwargs
        assert call_kwargs.get("source_srid") == 4326
