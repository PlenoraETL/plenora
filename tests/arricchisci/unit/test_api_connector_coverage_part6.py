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
# _extract_rows_from_response — array absolute paths
# =============================================================================

class TestExtractRowsArrayAbsolutePaths:

    def test_absolute_index_paths_produce_single_row(self, svc):
        """When all paths are [N].xxx, produce one row from the whole list."""
        data = [{"name": {"common": "Italy"}}, {"name": {"common": "France"}}]
        mapping = [
            {"path": "[0].name.common", "column": "first_country"},
            {"path": "[1].name.common", "column": "second_country"},
        ]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 1
        assert rows[0]["first_country"] == "Italy"
        assert rows[0]["second_country"] == "France"


# =============================================================================
# _execute_api_enricher  (lines 1502-1597)
# =============================================================================

class TestExecuteApiEnricher:

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_enriches_each_row(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"lat": 41.9, "lng": 12.4}
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"city": ["Rome", "Milan"]})
        api_config = {
            "endpoint": {"url": "http://example.com/geo", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [
                {"path": "lat", "column": "latitude"},
                {"path": "lng", "column": "longitude"},
            ],
            "options": {"rate_limit": 0},
        }
        result = svc._execute_api_enricher(df, api_config, {"q": "city"})
        assert "latitude" in result.columns
        assert "longitude" in result.columns
        assert result.at[0, "latitude"] == 41.9
        assert result.at[1, "longitude"] == 12.4

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_raises_when_no_url(self, mock_time, mock_requests):
        svc = _make_svc()
        df = pd.DataFrame({"city": ["Rome"]})
        api_config = {
            "endpoint": {"url": "", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [],
            "options": {},
        }
        with pytest.raises(ValueError, match="URL API non configurato"):
            svc._execute_api_enricher(df, api_config, {})

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_handles_non_200_response(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server Error"
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"city": ["Rome"]})
        api_config = {
            "endpoint": {"url": "http://example.com/geo", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [{"path": "lat", "column": "latitude"}],
            "options": {"rate_limit": 0},
        }
        result = svc._execute_api_enricher(df, api_config, {})
        # Output column exists but is None for failed rows
        assert result.at[0, "latitude"] is None

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_post_method(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}
        mock_requests.post.return_value = mock_resp

        df = pd.DataFrame({"city": ["Rome"]})
        api_config = {
            "endpoint": {"url": "http://example.com/api", "method": "POST"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [{"path": "result", "column": "res"}],
            "options": {"rate_limit": 0},
        }
        result = svc._execute_api_enricher(df, api_config, {"q": "city"})
        assert result.at[0, "res"] == "ok"
        mock_requests.post.assert_called()

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_rate_limiting(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"val": 1}
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"x": [1, 2]})
        api_config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [{"path": "val", "column": "val"}],
            "options": {"rate_limit": 5},
        }
        svc._execute_api_enricher(df, api_config, {})
        # rate_limit=5 means delay=0.2s, sleep called once per row
        assert mock_time.call_count == 2
        mock_time.assert_called_with(0.2)

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_api_key_query_in_enricher(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"val": 1}
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"x": [1]})
        api_config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "api_key", "location": "query", "key_name": "key", "key_value": "secret"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [{"path": "val", "column": "val"}],
            "options": {"rate_limit": 0},
        }
        svc._execute_api_enricher(df, api_config, {})
        call_kwargs = mock_requests.get.call_args
        assert call_kwargs[1]["params"]["key"] == "secret"

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_path_params_in_enricher_url(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"val": 1}
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"city": ["Rome"]})
        api_config = {
            "endpoint": {"url": "http://example.com/api/{city}", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {"city": "default_city"},
            "output_mapping": [{"path": "val", "column": "val"}],
            "options": {"rate_limit": 0},
        }
        svc._execute_api_enricher(df, api_config, {"city": "city"})
        call_url = mock_requests.get.call_args[0][0]
        assert "Rome" in call_url

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_exception_per_row_does_not_crash(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_requests.get.side_effect = RuntimeError("network error")

        df = pd.DataFrame({"x": [1, 2]})
        api_config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [],
            "options": {"rate_limit": 0},
        }
        # Should not raise, errors are caught per row
        result = svc._execute_api_enricher(df, api_config, {})
        assert len(result) == 2

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_static_parameters_used(self, mock_time, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"val": 1}
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"city": ["Rome"]})
        api_config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {"format": "json", "lang": "it"},
            "output_mapping": [{"path": "val", "column": "val"}],
            "options": {"rate_limit": 0},
        }
        svc._execute_api_enricher(df, api_config, {"q": "city"})
        call_kwargs = mock_requests.get.call_args
        params = call_kwargs[1]["params"]
        assert params["format"] == "json"
        assert params["lang"] == "it"

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("time.sleep")
    def test_na_values_not_mapped(self, mock_time, mock_requests):
        """pd.NA / NaN values should be mapped as None, not as string 'nan'."""
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"val": 1}
        mock_requests.get.return_value = mock_resp

        df = pd.DataFrame({"city": [None]})
        api_config = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [{"path": "val", "column": "val"}],
            "options": {"rate_limit": 0},
        }
        svc._execute_api_enricher(df, api_config, {"q": "city"})
        call_kwargs = mock_requests.get.call_args
        params = call_kwargs[1]["params"]
        # city is NaN, so q param should be None (not included or None)
        assert params.get("q") is None
