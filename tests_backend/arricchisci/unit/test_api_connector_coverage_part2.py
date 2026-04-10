"""
tests/arricchisci/unit/test_api_connector_coverage.py
=====================================================
Additional unit tests to cover missing lines in api_connector.py.

Missing lines targeted:
  132-148, 153-162, 166-204, 208-234, 238-242, 263-348, 380, 490,
  558, 579-580, 584, 602-607, 616, 630-631, 637-652, 673-677, 680,
  684-687, 707, 768, 772, 776, 780-852, 856-873, 881-893,
  921-1156, 1168-1223, 1250-1406, 1428, 1439, 1451-1452,
  1502-1597
"""

import pytest
import json
import pandas as pd
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.arricchisci.api_connector import ApiConnectionService, _nd


# ─── Helper ──────────────────────────────────────────────────────────────────

def _make_svc():
    """Create an ApiConnectionService without calling __init__."""
    with patch("app.core.arricchisci.api_connector.settings") as mock_settings:
        mock_settings.DATA_DIR = Path("/tmp")
        mock_settings.RESULTS_DIR = Path("/tmp")
        mock_settings.API_CONNECTIONS_DIR = Path("/tmp")
        mock_settings.DEFAULT_GROUP = "default"
        svc = ApiConnectionService.__new__(ApiConnectionService)
        svc.data_dir = Path("/tmp")
        svc.results_dir = Path("/tmp")
        svc.api_connections_dir = Path("/tmp")
    return svc


@pytest.fixture
def svc():
    return _make_svc()




# =============================================================================
# test_api_connection  (lines 263-348)
# =============================================================================

class TestTestApiConnection:

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_returns_error_when_api_id_not_found(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = None
        result = svc.test_api_connection(api_id="nonexistent")
        assert result["success"] is False
        assert "non trovata" in result["error"]

    def test_returns_error_when_no_config(self):
        svc = _make_svc()
        result = svc.test_api_connection()
        assert result["success"] is False
        assert "Nessuna configurazione" in result["error"]

    def test_returns_error_when_url_empty(self):
        svc = _make_svc()
        config = {"endpoint": {"url": "", "method": "GET"}, "auth": {"type": "none"}}
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "URL non configurato" in result["error"]

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("app.core.arricchisci.api_connector.process_static_fields")
    def test_get_request_with_json_response(self, mock_process, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"name":"Italy"}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {"name": "Italy"}
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config, test_params={"q": "test"})
        assert result["success"] is True
        assert result["status_code"] == 200
        assert "available_fields" in result
        assert result["raw_response"] == {"name": "Italy"}

    @patch("app.core.arricchisci.api_connector.requests")
    def test_post_request(self, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {"ok": True}
        mock_requests.post.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "POST"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is True
        mock_requests.post.assert_called_once()

    @patch("app.core.arricchisci.api_connector.requests")
    def test_non_200_status_returns_error(self, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "HTTP 404" in result["error"]

    @patch("app.core.arricchisci.api_connector.requests")
    def test_non_json_content_type_returns_error(self, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>stuff</html>"
        mock_response.headers = {"Content-Type": "text/html"}
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "Content-Type non è JSON" in result["error"]

    @patch("app.core.arricchisci.api_connector.requests")
    def test_json_decode_error(self, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "not json"
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.side_effect = json.JSONDecodeError("err", "doc", 0)
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "non è JSON valido" in result["error"]

    @patch("app.core.arricchisci.api_connector.requests")
    def test_timeout_returns_error(self, mock_requests):
        svc = _make_svc()
        mock_requests.get.side_effect = mock_requests.exceptions.Timeout("timeout")
        mock_requests.exceptions.Timeout = type("Timeout", (Exception,), {})
        mock_requests.get.side_effect = mock_requests.exceptions.Timeout("timeout")

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "Timeout" in result["error"]

    @patch("app.core.arricchisci.api_connector.requests")
    def test_connection_error_returns_error(self, mock_requests):
        svc = _make_svc()
        mock_requests.exceptions.Timeout = type("Timeout", (Exception,), {})
        mock_requests.exceptions.ConnectionError = type("ConnectionError", (Exception,), {})
        mock_requests.get.side_effect = mock_requests.exceptions.ConnectionError("refused")

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "connessione" in result["error"]

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("app.core.arricchisci.api_connector.process_static_fields")
    def test_template_params_processed(self, mock_process, mock_requests):
        """Template variables like {{NOW}} are processed before the call."""
        svc = _make_svc()
        mock_process.return_value = {"date": "2026-04-06"}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {"ok": True}
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config, test_params={"date": "{{NOW}}"})
        assert result["success"] is True
        mock_process.assert_called()

    @patch("app.core.arricchisci.api_connector.requests")
    def test_path_params_substituted_in_url(self, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {"ok": True}
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api/{name}", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config, test_params={"name": "Italy"})
        assert result["success"] is True
        # Path param consumed, not sent as query param
        call_args = mock_requests.get.call_args
        assert "Italy" in call_args[0][0]  # in URL
        assert "name" not in call_args[1].get("params", {})

    @patch("app.core.arricchisci.api_connector.requests")
    def test_api_key_query_added(self, mock_requests):
        svc = _make_svc()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {"ok": True}
        mock_requests.get.return_value = mock_response
        mock_requests.exceptions = MagicMock()

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "api_key", "location": "query", "key_name": "apiKey", "key_value": "abc123"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        call_args = mock_requests.get.call_args
        assert call_args[1]["params"]["apiKey"] == "abc123"

    @patch("app.core.arricchisci.api_connector.requests")
    def test_generic_exception_returns_error(self, mock_requests):
        svc = _make_svc()
        mock_requests.exceptions.Timeout = type("Timeout", (Exception,), {})
        mock_requests.exceptions.ConnectionError = type("ConnectionError", (Exception,), {})
        mock_requests.get.side_effect = RuntimeError("something broke")

        config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
        }
        result = svc.test_api_connection(config=config)
        assert result["success"] is False
        assert "something broke" in result["error"]


# =============================================================================
# _extract_available_fields — array with prefix (line 380)
# =============================================================================

class TestExtractAvailableFieldsExtraLines:

    def test_array_with_prefix_uses_bracket_notation(self, svc):
        """Line 380: nested array with prefix uses [0] indexing."""
        data = {"items": [{"id": 1}, {"id": 2}]}
        fields = svc._extract_available_fields(data)
        paths = [f["path"] for f in fields]
        assert "items" in paths
        assert "items[0].id" in paths


# =============================================================================
# _get_field_type unknown type (line 490)
# =============================================================================

class TestGetFieldTypeUnknown:

    def test_unknown_type_returns_unknown(self, svc):
        """Line 490: non-standard type returns 'unknown'."""
        # Use a custom class that is not dict/list/str/int/float/bool/None
        assert svc._get_field_type(object()) == "unknown"
        assert svc._get_field_type(set()) == "unknown"
        assert svc._get_field_type(b"bytes") == "unknown"


# =============================================================================
# _prepare_headers / _get_oauth2_token / _get_oauth2_cc_token (lines 768, 772, 776)
# =============================================================================

class TestDelegatedMethods:

    @patch("app.core.arricchisci.api_connector._shared_prepare_headers")
    def test_prepare_headers_delegates(self, mock_shared):
        svc = _make_svc()
        mock_shared.return_value = {"Authorization": "Bearer token"}
        result = svc._prepare_headers({"type": "bearer", "token": "token"}, {})
        assert result == {"Authorization": "Bearer token"}
        mock_shared.assert_called_once()

    @patch("app.core.arricchisci.api_connector._shared_get_oauth2_cc_token")
    def test_get_oauth2_cc_token_delegates(self, mock_shared):
        svc = _make_svc()
        mock_shared.return_value = "token123"
        result = svc._get_oauth2_client_credentials_token({"type": "oauth2_client_credentials"})
        assert result == "token123"

    @patch("app.core.arricchisci.api_connector._shared_get_oauth2_token")
    def test_get_oauth2_token_delegates(self, mock_shared):
        svc = _make_svc()
        mock_shared.return_value = "token456"
        result = svc._get_oauth2_token({"type": "oauth2_password"})
        assert result == "token456"


# =============================================================================
# _load_source_project  (lines 780-852)
# =============================================================================

class TestLoadSourceProject:

    def test_raises_when_no_source_project_id(self):
        svc = _make_svc()
        with pytest.raises(ValueError, match="source_project_id non specificato"):
            svc._load_source_project({})

    @patch("app.core.arricchisci.api_connector.filter_visible_columns")
    @patch("app.utils.dataframe.load_dataframe_from_file")
    @patch("app.core.arricchisci.api_connector.find_latest_output_file")
    def test_loads_from_blob_db(self, mock_find, mock_load, mock_filter, tmp_path):
        svc = _make_svc()
        results_dir = tmp_path / "results"
        (results_dir / "proj_1").mkdir(parents=True)
        svc.results_dir = results_dir
        df = pd.DataFrame({"a": [1, 2]})
        mock_filter.return_value = df

        with patch("app.utils.result_store.get_result_as_tempfile") as mock_blob, \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink"):
            mock_blob.return_value = "/tmp/tempfile.csv"
            mock_load.return_value = df
            result = svc._load_source_project(
                {"source_project_id": "proj_1"},
                force_recalculate=False,
            )
            assert len(result) == 2

    @patch("app.core.arricchisci.api_connector.filter_visible_columns")
    @patch("app.utils.dataframe.load_dataframe_from_file")
    @patch("app.core.arricchisci.api_connector.find_latest_output_file")
    def test_falls_back_to_filesystem_when_blob_fails(self, mock_find, mock_load, mock_filter, tmp_path):
        svc = _make_svc()
        results_dir = tmp_path / "results"
        (results_dir / "proj_1").mkdir(parents=True)
        svc.results_dir = results_dir
        df = pd.DataFrame({"a": [1]})
        mock_filter.return_value = df
        mock_find.return_value = results_dir / "proj_1" / "output.csv"
        mock_load.return_value = df

        with patch("app.utils.result_store.get_result_as_tempfile") as mock_blob:
            mock_blob.side_effect = Exception("blob unavailable")
            result = svc._load_source_project({"source_project_id": "proj_1"})
            assert len(result) == 1

    @patch("app.core.arricchisci.api_connector.filter_visible_columns")
    @patch("app.utils.dataframe.load_dataframe_from_file")
    @patch("app.core.arricchisci.api_connector.find_latest_output_file")
    def test_raises_when_no_output_file(self, mock_find, mock_load, mock_filter, tmp_path):
        svc = _make_svc()
        results_dir = tmp_path / "results"
        (results_dir / "proj_1").mkdir(parents=True)
        svc.results_dir = results_dir
        mock_find.return_value = None

        with patch("app.utils.result_store.get_result_as_tempfile") as mock_blob:
            mock_blob.return_value = None
            mock_load.return_value = None
            # When blob returns None and no filesystem file, should raise
            with pytest.raises(ValueError, match="Nessun output"):
                svc._load_source_project({"source_project_id": "proj_1"})

    @patch("app.core.arricchisci.api_connector.filter_visible_columns")
    @patch("app.utils.dataframe.load_dataframe_from_file")
    @patch("app.core.arricchisci.api_connector.find_latest_output_file")
    def test_filters_visible_columns(self, mock_find, mock_load, mock_filter, tmp_path):
        svc = _make_svc()
        results_dir = tmp_path / "results"
        (results_dir / "proj_1").mkdir(parents=True)
        svc.results_dir = results_dir
        df = pd.DataFrame({"a": [1], "b": [2]})
        filtered = pd.DataFrame({"a": [1]})
        mock_filter.return_value = filtered

        with patch("app.utils.result_store.get_result_as_tempfile") as mock_blob, \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink"):
            mock_blob.return_value = "/tmp/tempfile.csv"
            mock_load.return_value = df
            result = svc._load_source_project(
                {"source_project_id": "proj_1", "columns": [{"name": "a", "visible": True}]},
            )
            mock_filter.assert_called_once()
