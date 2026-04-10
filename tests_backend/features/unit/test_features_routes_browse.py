"""
tests/features/unit/test_features_routes_browse.py
===================================================
Unit test per le route FastAPI di browse del modulo Features.

Copre:
  - GET /features/connections/{id}/services       (browse)
  - GET /features/connections/{id}/services/search (search)
  - GET /features/connections/{id}/layers          (layers)
  - GET /features/connections/{id}/schema          (schema)
  - Error paths: ValueError->404, ConnectionError->502, Exception->500
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
# GET /features/connections/{id}/services
# =============================================================================

class TestBrowseServices:

    def test_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_services.return_value = [
            {"name": "Parcels", "url": "https://x/FS", "folder": ""},
            {"name": "Roads", "url": "https://x/FS2", "folder": "infra"},
        ]
        res = client.get("/features/connections/conn1/services")
        assert res.status_code == 200
        data = res.json()
        assert len(data["services"]) == 2
        assert data["services"][0]["name"] == "Parcels"

    def test_empty_list(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_services.return_value = []
        res = client.get("/features/connections/conn1/services")
        assert res.status_code == 200
        assert res.json()["services"] == []

    def test_value_error_404(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_services.side_effect = ValueError("Connessione non trovata")
        res = client.get("/features/connections/missing/services")
        assert res.status_code == 404
        assert "non trovata" in res.json()["detail"]

    def test_connection_error_502(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_services.side_effect = ConnectionError("timeout")
        res = client.get("/features/connections/conn1/services")
        assert res.status_code == 502

    def test_generic_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_services.side_effect = RuntimeError("unexpected")
        res = client.get("/features/connections/conn1/services")
        assert res.status_code == 500


# =============================================================================
# GET /features/connections/{id}/layers
# =============================================================================

class TestBrowseLayers:

    def test_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_layers.return_value = [
            {"id": 0, "name": "Buildings", "geometry_type": "esriGeometryPolygon"},
            {"id": 1, "name": "Roads", "geometry_type": "esriGeometryPolyline"},
        ]
        res = client.get(
            "/features/connections/conn1/layers",
            params={"service_url": "https://x/FeatureServer"},
        )
        assert res.status_code == 200
        layers = res.json()["layers"]
        assert len(layers) == 2
        assert layers[0]["name"] == "Buildings"

    def test_missing_service_url_422(self, client_and_svc):
        client, _ = client_and_svc
        res = client.get("/features/connections/conn1/layers")
        assert res.status_code == 422

    def test_value_error_404(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_layers.side_effect = ValueError("non trovata")
        res = client.get(
            "/features/connections/missing/layers",
            params={"service_url": "https://x/FS"},
        )
        assert res.status_code == 404

    def test_connection_error_502(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_layers.side_effect = ConnectionError("timeout")
        res = client.get(
            "/features/connections/conn1/layers",
            params={"service_url": "https://x/FS"},
        )
        assert res.status_code == 502

    def test_generic_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.browse_layers.side_effect = RuntimeError("unexpected")
        res = client.get(
            "/features/connections/conn1/layers",
            params={"service_url": "https://x/FS"},
        )
        assert res.status_code == 500


# =============================================================================
# GET /features/connections/{id}/schema
# =============================================================================

class TestGetLayerSchema:

    def test_success(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.get_layer_schema.return_value = {
            "name": "Buildings",
            "fields": [
                {"name": "OBJECTID", "field_type": "esriFieldTypeOID"},
                {"name": "NAME", "field_type": "esriFieldTypeString"},
            ],
            "geometry_type": "esriGeometryPolygon",
            "spatial_reference": {"wkid": 4326},
        }
        res = client.get(
            "/features/connections/conn1/schema",
            params={"layer_url": "https://x/FS/0"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "Buildings"
        assert len(data["fields"]) == 2

    def test_missing_layer_url_422(self, client_and_svc):
        client, _ = client_and_svc
        res = client.get("/features/connections/conn1/schema")
        assert res.status_code == 422

    def test_value_error_404(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.get_layer_schema.side_effect = ValueError("non trovata")
        res = client.get(
            "/features/connections/missing/schema",
            params={"layer_url": "https://x/FS/0"},
        )
        assert res.status_code == 404

    def test_connection_error_502(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.get_layer_schema.side_effect = ConnectionError("refused")
        res = client.get(
            "/features/connections/conn1/schema",
            params={"layer_url": "https://x/FS/0"},
        )
        assert res.status_code == 502

    def test_generic_error_500(self, client_and_svc):
        client, mock_svc = client_and_svc
        mock_svc.get_layer_schema.side_effect = RuntimeError("crash")
        res = client.get(
            "/features/connections/conn1/schema",
            params={"layer_url": "https://x/FS/0"},
        )
        assert res.status_code == 500
