"""
tests/features/unit/test_features_service_ops.py
=================================================
Unit test per FeaturesService — operazioni browse, query, execute, write, create.

Copre:
  - browse_services, browse_layers, get_layer_schema, search_services
  - query_layer, execute_query (con salvataggio file)
  - write_features (add/update/delete)
  - create_service
  - _get_layer_crs, _load_dataframe, _salvataggio_default
"""

import pytest
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from app.core.features.models import (
    ArcGISConnection, ArcGISCreate, ArcGISList, ArcGISTestResult,
)
from app.core.features.arcgis_client import ArcGISConnectionInfo


def _now():
    return datetime.now(timezone.utc)


def _make_connection(**overrides) -> ArcGISConnection:
    defaults = dict(
        id="arcgis_test_20260101_000000_abcd1234",
        name="Test", description="",
        server_url="https://server.example.com/arcgis",
        server_type="arcgis_server", auth_type="token",
        username="user", password="pass",
        client_id="", client_secret="", token_url=None,
        created_at=_now(), updated_at=_now(),
    )
    defaults.update(overrides)
    return ArcGISConnection(**defaults)


def _make_service(client=None, save_function=None, results_dir=None):
    from app.core.features.features_service import FeaturesService
    svc = FeaturesService.__new__(FeaturesService)
    svc._client = client or MagicMock()
    svc._save = save_function or MagicMock(return_value=Path("/tmp/out.csv"))
    svc._arcgis_dir = MagicMock()
    svc._results_dir = results_dir or Path("/tmp/results")
    return svc


# =============================================================================
# browse_services
# =============================================================================

class TestBrowseServices:

    @patch("app.core.features.features_service.get_connection_db")
    def test_success(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.list_services.return_value = [
            {"name": "Parcels", "url": "https://x/FeatureServer", "folder": ""}
        ]
        svc = _make_service(client=mock_client)
        result = svc.browse_services(conn.id)
        assert len(result) == 1
        assert result[0]["name"] == "Parcels"

    @patch("app.core.features.features_service.get_connection_db")
    def test_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.browse_services("missing")


# =============================================================================
# search_services
# =============================================================================

class TestSearchServices:

    @patch("app.core.features.features_service.get_connection_db")
    def test_online_delegates_to_search(self, mock_get):
        conn = _make_connection(server_url="https://www.arcgis.com")
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client._is_arcgis_online.return_value = True
        mock_client.search_services_online.return_value = [{"name": "Found"}]
        svc = _make_service(client=mock_client)
        result = svc.search_services(conn.id, query="catasto", scope="org")
        mock_client.search_services_online.assert_called_once()
        assert result[0]["name"] == "Found"

    @patch("app.core.features.features_service.get_connection_db")
    def test_server_falls_back_to_list(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client._is_arcgis_online.return_value = False
        mock_client.list_services.return_value = [{"name": "Svc"}]
        svc = _make_service(client=mock_client)
        result = svc.search_services(conn.id)
        mock_client.list_services.assert_called_once()
        assert result[0]["name"] == "Svc"

    @patch("app.core.features.features_service.get_connection_db")
    def test_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.search_services("missing")


# =============================================================================
# browse_layers
# =============================================================================

class TestBrowseLayers:

    @patch("app.core.features.features_service.get_connection_db")
    def test_success(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.list_layers.return_value = [
            {"id": 0, "name": "Buildings", "geometry_type": "esriGeometryPolygon"}
        ]
        svc = _make_service(client=mock_client)
        result = svc.browse_layers(conn.id, "https://x/FeatureServer")
        assert result[0]["name"] == "Buildings"

    @patch("app.core.features.features_service.get_connection_db")
    def test_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.browse_layers("missing", "https://x/FeatureServer")


# =============================================================================
# get_layer_schema
# =============================================================================

class TestGetLayerSchema:

    @patch("app.core.features.features_service.get_connection_db")
    def test_success(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.get_layer_schema.return_value = {
            "name": "Layer0",
            "fields": [{"name": "OID"}],
            "spatial_reference": {"wkid": 4326},
        }
        svc = _make_service(client=mock_client)
        result = svc.get_layer_schema(conn.id, "https://x/FeatureServer/0")
        assert result["name"] == "Layer0"

    @patch("app.core.features.features_service.get_connection_db")
    def test_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.get_layer_schema("missing", "https://x/FeatureServer/0")


# =============================================================================
# query_layer
# =============================================================================

class TestQueryLayer:

    @patch("app.core.features.features_service.get_connection_db")
    def test_returns_rows_columns(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.query_features.return_value = (
            [(1, "A"), (2, "B")],
            ["id", "name"],
        )
        svc = _make_service(client=mock_client)
        rows, cols = svc.query_layer(conn.id, "https://x/FS/0", where="id>0")
        assert len(rows) == 2
        assert cols == ["id", "name"]

    @patch("app.core.features.features_service.get_connection_db")
    def test_passes_all_params(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.query_features.return_value = ([], [])
        svc = _make_service(client=mock_client)
        svc.query_layer(
            conn.id, "url", where="x=1", out_fields="a,b",
            return_geometry=False, max_features=10, out_sr=32632,
        )
        call_kwargs = mock_client.query_features.call_args
        assert call_kwargs.kwargs["where"] == "x=1"
        assert call_kwargs.kwargs["max_features"] == 10
        assert call_kwargs.kwargs["out_sr"] == 32632

    @patch("app.core.features.features_service.get_connection_db")
    def test_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.query_layer("missing", "url")


# =============================================================================
# execute_query
# =============================================================================

class TestExecuteQuery:

    @patch("app.core.features.features_service.save_result_blob")
    @patch("app.core.features.features_service.get_temp_output_path")
    @patch("app.core.features.features_service.get_connection_db")
    def test_success_csv(self, mock_get, mock_temp_path, mock_save_blob, tmp_path):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_temp_path.return_value = tmp_path / "output"

        mock_client = MagicMock()
        mock_client.query_features.return_value = (
            [(1, "Rome")], ["id", "city"]
        )
        mock_client.get_layer_schema.return_value = {
            "spatial_reference": {"wkid": 4326}
        }
        mock_save = MagicMock(return_value=tmp_path / "output.csv")

        svc = _make_service(client=mock_client, save_function=mock_save, results_dir=tmp_path)
        result = svc.execute_query(
            connection_id=conn.id,
            layer_url="https://x/FS/0",
            project_id="proj1",
            output_filename="test_out",
            output_format="csv",
        )
        assert result["success"] is True
        assert result["rows_count"] == 1
        assert result["columns"] == ["id", "city"]
        assert "timings" in result
        mock_save.assert_called_once()

    @patch("app.core.features.features_service.get_connection_db")
    def test_query_error_returns_failure(self, mock_get, tmp_path):
        conn = _make_connection()
        mock_get.return_value = conn

        mock_client = MagicMock()
        mock_client.query_features.side_effect = ConnectionError("timeout")
        mock_client.get_layer_schema.return_value = {"spatial_reference": {}}

        svc = _make_service(client=mock_client, results_dir=tmp_path)
        result = svc.execute_query(
            connection_id=conn.id,
            layer_url="https://x/FS/0",
            project_id="proj1",
            output_filename="out",
        )
        assert result["success"] is False
        assert "timeout" in result["message"]

    @patch("app.core.features.features_service.save_result_blob")
    @patch("app.core.features.features_service.get_temp_output_path")
    @patch("app.core.features.features_service.get_connection_db")
    def test_no_geometry(self, mock_get, mock_temp_path, mock_save_blob, tmp_path):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_temp_path.return_value = tmp_path / "output"

        mock_client = MagicMock()
        mock_client.query_features.return_value = ([(1,)], ["id"])
        mock_client.get_layer_schema.return_value = {"spatial_reference": {}}

        mock_save = MagicMock(return_value=tmp_path / "output.csv")
        svc = _make_service(client=mock_client, save_function=mock_save, results_dir=tmp_path)
        result = svc.execute_query(
            connection_id=conn.id,
            layer_url="url",
            project_id="p",
            output_filename="f",
            return_geometry=False,
        )
        assert result["success"] is True
        # geom_column should be None when return_geometry=False
        call_kwargs = mock_save.call_args.kwargs
        assert call_kwargs.get("geom_column") is None


# =============================================================================
# _get_layer_crs
# =============================================================================

class TestGetLayerCrs:

    @patch("app.core.features.features_service.get_connection_db")
    def test_explicit_out_sr(self, mock_get):
        svc = _make_service()
        result = svc._get_layer_crs("any", "url", out_sr=32632)
        assert result == "EPSG:32632"
        mock_get.assert_not_called()

    @patch("app.core.features.features_service.get_connection_db")
    def test_from_schema_wkid(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.get_layer_schema.return_value = {
            "spatial_reference": {"wkid": 3857, "latestWkid": 3857}
        }
        svc = _make_service(client=mock_client)
        result = svc._get_layer_crs(conn.id, "url")
        assert result == "EPSG:3857"

    @patch("app.core.features.features_service.get_connection_db")
    def test_fallback_4326(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.get_layer_schema.return_value = {"spatial_reference": {}}
        svc = _make_service(client=mock_client)
        result = svc._get_layer_crs(conn.id, "url")
        assert result == "EPSG:4326"

    @patch("app.core.features.features_service.get_connection_db")
    def test_connection_not_found_fallback(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        result = svc._get_layer_crs("missing", "url")
        assert result == "EPSG:4326"

    @patch("app.core.features.features_service.get_connection_db")
    def test_exception_fallback(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.get_layer_schema.side_effect = Exception("fail")
        svc = _make_service(client=mock_client)
        result = svc._get_layer_crs(conn.id, "url")
        assert result == "EPSG:4326"


# =============================================================================
# write_features
# =============================================================================

class TestWriteFeatures:

    @patch("app.core.features.features_service.get_connection_db")
    def test_delete_operation(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.delete_features.return_value = {
            "success": True, "deleted_count": 5, "errors": []
        }
        svc = _make_service(client=mock_client)
        result = svc.write_features(
            conn.id, "https://x/FS/0", "delete",
            input_path="", object_ids=[1, 2, 3, 4, 5],
        )
        assert result["success"] is True
        assert result["count"] == 5

    @patch("app.core.features.features_service.get_connection_db")
    def test_delete_with_where(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.delete_features.return_value = {
            "success": True, "deleted_count": 2, "errors": []
        }
        svc = _make_service(client=mock_client)
        result = svc.write_features(
            conn.id, "url", "delete", input_path="", where="STATUS='old'",
        )
        assert result["count"] == 2

    @patch("app.core.features.features_service.get_connection_db")
    def test_add_operation(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.dataframe_to_esri_features.return_value = [{"attr": {}}]
        mock_client.add_features.return_value = {
            "success": True, "added_count": 1, "errors": []
        }
        svc = _make_service(client=mock_client)

        with patch.object(type(svc), '_load_dataframe', return_value=pd.DataFrame({"a": [1]})):
            result = svc.write_features(conn.id, "url", "add", input_path="/tmp/f.csv")
        assert result["success"] is True
        assert result["count"] == 1

    @patch("app.core.features.features_service.get_connection_db")
    def test_update_operation(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.dataframe_to_esri_features.return_value = [{"attr": {}}]
        mock_client.update_features.return_value = {
            "success": True, "updated_count": 3, "errors": []
        }
        svc = _make_service(client=mock_client)

        with patch.object(type(svc), '_load_dataframe', return_value=pd.DataFrame({"a": [1, 2, 3]})):
            result = svc.write_features(conn.id, "url", "update", input_path="/tmp/f.csv")
        assert result["success"] is True
        assert result["count"] == 3

    @patch("app.core.features.features_service.get_connection_db")
    def test_no_features_returns_failure(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.dataframe_to_esri_features.return_value = []
        svc = _make_service(client=mock_client)

        with patch.object(type(svc), '_load_dataframe', return_value=pd.DataFrame()):
            result = svc.write_features(conn.id, "url", "add", input_path="/tmp/f.csv")
        assert result["success"] is False
        assert result["count"] == 0

    @patch("app.core.features.features_service.get_connection_db")
    def test_add_without_input_path_raises(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        svc = _make_service()
        with pytest.raises(ValueError, match="input_path"):
            svc.write_features(conn.id, "url", "add", input_path="")

    @patch("app.core.features.features_service.get_connection_db")
    def test_unsupported_operation_raises(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        svc = _make_service()
        mock_client = MagicMock()
        mock_client.dataframe_to_esri_features.return_value = [{"a": 1}]
        svc._client = mock_client

        with patch.object(type(svc), '_load_dataframe', return_value=pd.DataFrame({"a": [1]})):
            with pytest.raises(ValueError, match="non supportata"):
                svc.write_features(conn.id, "url", "merge", input_path="/tmp/f.csv")

    @patch("app.core.features.features_service.get_connection_db")
    def test_connection_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.write_features("missing", "url", "add", input_path="/tmp/f.csv")


# =============================================================================
# create_service
# =============================================================================

class TestCreateService:

    @patch("app.core.features.features_service.get_connection_db")
    def test_success(self, mock_get):
        conn = _make_connection()
        mock_get.return_value = conn
        mock_client = MagicMock()
        mock_client.create_service.return_value = {
            "success": True,
            "service_url": "https://x/FS",
            "layer_url": "https://x/FS/0",
            "feature_count": 10,
        }
        svc = _make_service(client=mock_client)

        with patch.object(type(svc), '_load_dataframe', return_value=pd.DataFrame({"a": range(10)})):
            result = svc.create_service(
                conn.id, "NewService", "Layer0",
                input_path="/tmp/f.csv",
            )
        assert result["success"] is True
        assert result["feature_count"] == 10

    @patch("app.core.features.features_service.get_connection_db")
    def test_connection_not_found_raises(self, mock_get):
        mock_get.return_value = None
        svc = _make_service()
        with pytest.raises(ValueError, match="non trovata"):
            svc.create_service("missing", "Svc", "L0", input_path="/tmp/f.csv")


# =============================================================================
# _salvataggio_default (static)
# =============================================================================

class TestSalvataggioDefault:

    def test_csv(self, tmp_path):
        from app.core.features.features_service import FeaturesService
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        out = tmp_path / "out"
        result = FeaturesService._salvataggio_default(df, out, "csv")
        assert result.suffix == ".csv"
        assert result.exists()

    def test_json(self, tmp_path):
        from app.core.features.features_service import FeaturesService
        df = pd.DataFrame({"a": [1]})
        result = FeaturesService._salvataggio_default(df, tmp_path / "out", "json")
        assert result.suffix == ".json"

    def test_unknown_format_falls_back_csv(self, tmp_path):
        from app.core.features.features_service import FeaturesService
        df = pd.DataFrame({"a": [1]})
        result = FeaturesService._salvataggio_default(df, tmp_path / "out", "parquet_unsupported")
        assert result.suffix == ".csv"
