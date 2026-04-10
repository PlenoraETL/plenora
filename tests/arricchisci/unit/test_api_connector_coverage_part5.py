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
# _execute_api_generator  (lines 1250-1406)
# =============================================================================

class TestExecuteApiGenerator:

    def _make_api_config(self, **overrides):
        base = {
            "endpoint": {"url": "http://example.com/api", "method": "GET"},
            "auth": {"type": "none"},
            "custom_headers": {},
            "static_parameters": {},
            "output_mapping": [{"path": "name", "column": "name"}],
            "response_config": {},
            "response_preset": "",
            "pagination": {},
        }
        base.update(overrides)
        return base

    @patch("app.core.arricchisci.api_connector.requests")
    def test_no_pagination_single_call(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "Italy"}, {"name": "France"}]
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config()
        result = svc._execute_api_generator(api_config)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert result.iloc[0]["name"] == "Italy"

    @patch("app.core.arricchisci.api_connector.requests")
    def test_raises_when_no_url(self, mock_requests):
        svc = _make_svc()
        api_config = self._make_api_config()
        api_config["endpoint"]["url"] = ""
        with pytest.raises(ValueError, match="URL API non configurato"):
            svc._execute_api_generator(api_config)

    @patch("app.core.arricchisci.api_connector.requests")
    def test_uses_input_df_params(self, mock_requests):
        """When input_df is provided, uses first row as params."""
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "Result"}
        mock_requests.get.return_value = mock_resp

        input_df = pd.DataFrame({"city": ["Rome"], "country": ["Italy"]})
        api_config = self._make_api_config()
        svc._execute_api_generator(api_config, input_df)
        call_kwargs = mock_requests.get.call_args
        # Params from input_df should be in the call
        assert "city" in call_kwargs[1]["params"] or "city" in str(call_kwargs)

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("app.core.arricchisci.api_connector.process_static_fields")
    def test_uses_static_parameters(self, mock_process, mock_requests):
        svc = _make_svc()
        mock_process.return_value = {"q": "Rome"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "Rome"}
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config(static_parameters={"q": "{{CITY}}"})
        svc._execute_api_generator(api_config)
        mock_process.assert_called()

    @patch("app.core.arricchisci.api_connector.requests")
    def test_non_200_raises_valueerror(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config()
        with pytest.raises(ValueError, match="HTTP 500"):
            svc._execute_api_generator(api_config)

    @patch("app.core.arricchisci.api_connector.requests")
    def test_post_method(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "Italy"}]
        mock_requests.post.return_value = mock_resp

        api_config = self._make_api_config()
        api_config["endpoint"]["method"] = "POST"
        result = svc._execute_api_generator(api_config)
        mock_requests.post.assert_called_once()
        assert len(result) == 1

    @patch("app.core.arricchisci.api_connector.requests")
    def test_api_key_query_param(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "Italy"}]
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config(
            auth={"type": "api_key", "location": "query", "key_name": "myKey", "key_value": "secret"},
        )
        svc._execute_api_generator(api_config)
        call_kwargs = mock_requests.get.call_args
        assert call_kwargs[1]["params"]["myKey"] == "secret"

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("app.core.arricchisci.api_connector.get_preset_config")
    @patch("app.core.arricchisci.api_connector.ResponseParser")
    def test_response_preset_used(self, MockParser, mock_preset, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"name": "Italy"}]}
        mock_requests.get.return_value = mock_resp

        mock_preset.return_value = {"some": "config"}
        parser_instance = MockParser.return_value
        parser_instance.parse.return_value = [{"name": "Italy"}]

        api_config = self._make_api_config(response_preset="custom_preset")
        result = svc._execute_api_generator(api_config)
        mock_preset.assert_called_with("custom_preset")
        assert len(result) == 1

    @patch("app.core.arricchisci.api_connector.requests")
    @patch("app.core.arricchisci.api_connector.ResponseParser")
    def test_response_config_used_when_no_wildcard(self, MockParser, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"nested": {"items": [{"name": "Italy"}]}}
        mock_requests.get.return_value = mock_resp

        parser_instance = MockParser.return_value
        parser_instance.parse.return_value = [{"name": "Italy"}]

        api_config = self._make_api_config(
            response_config={"mappings": [{"path": "nested.items", "column": "items"}]},
            response_preset="",
        )
        result = svc._execute_api_generator(api_config)
        assert len(result) == 1

    @patch("app.core.arricchisci.api_connector.requests")
    def test_response_config_with_wildcard_falls_back_to_extract_rows(self, mock_requests):
        """B-BE-30: wildcard paths in response_config should use _extract_rows_from_response."""
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": 1}, {"id": 2}]}
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config(
            response_config={"mappings": [{"path": "data[*].id", "column": "id"}]},
            output_mapping=[{"path": "data[*].id", "column": "id"}],
            response_preset="",
        )
        result = svc._execute_api_generator(api_config)
        assert isinstance(result, pd.DataFrame)

    # ── Pagination tests ──

    @patch("app.core.arricchisci.api_connector.requests")
    def test_offset_pagination(self, mock_requests):
        svc = _make_svc()
        call_count = [0]

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count[0] <= 2:
                resp.json.return_value = [{"name": f"item_{call_count[0]}"}]
            else:
                resp.json.return_value = []
            return resp

        mock_requests.get.side_effect = mock_get

        api_config = self._make_api_config(
            pagination={"type": "offset", "page_size": 1, "max_rows": 10},
        )
        result = svc._execute_api_generator(api_config)
        assert len(result) == 2

    @patch("app.core.arricchisci.api_connector.requests")
    def test_offset_pagination_respects_max_rows(self, mock_requests):
        svc = _make_svc()

        def mock_get(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [{"name": "item"}] * 5
            return resp

        mock_requests.get.side_effect = mock_get

        api_config = self._make_api_config(
            pagination={"type": "offset", "page_size": 5, "max_rows": 3},
        )
        result = svc._execute_api_generator(api_config)
        assert len(result) == 3

    @patch("app.core.arricchisci.api_connector.requests")
    def test_cursor_pagination(self, mock_requests):
        svc = _make_svc()
        call_count = [0]

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count[0] == 1:
                resp.json.return_value = {"items": [{"name": "a"}], "next_cursor": "abc"}
            elif call_count[0] == 2:
                resp.json.return_value = {"items": [{"name": "b"}], "next_cursor": None}
            else:
                resp.json.return_value = {"items": []}
            return resp

        mock_requests.get.side_effect = mock_get

        api_config = self._make_api_config(
            pagination={"type": "cursor", "page_size": 1, "max_rows": 100,
                        "cursor_param": "cursor", "cursor_path": "next_cursor"},
            output_mapping=[{"path": "items[*].name", "column": "name"}],
        )
        result = svc._execute_api_generator(api_config)
        assert isinstance(result, pd.DataFrame)

    @patch("app.core.arricchisci.api_connector.requests")
    def test_link_pagination(self, mock_requests):
        svc = _make_svc()
        call_count = [0]

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            if call_count[0] == 1:
                resp.json.return_value = {"data": [{"name": "a"}], "next": "http://example.com/page2"}
            elif call_count[0] == 2:
                resp.json.return_value = {"data": [{"name": "b"}], "next": None}
            else:
                resp.json.return_value = {"data": []}
            return resp

        mock_requests.get.side_effect = mock_get

        api_config = self._make_api_config(
            pagination={"type": "link", "page_size": 1, "max_rows": 100, "link_path": "next"},
            output_mapping=[{"path": "name", "column": "name"}],
        )
        result = svc._execute_api_generator(api_config)
        assert isinstance(result, pd.DataFrame)

    @patch("app.core.arricchisci.api_connector.requests")
    def test_link_pagination_fallback_to_header(self, mock_requests):
        """When link_path returns None, falls back to Link header RFC 5988."""
        svc = _make_svc()
        call_count = [0]

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count[0] == 1:
                resp.json.return_value = [{"name": "a"}]
                resp.headers = {"Link": '<http://example.com/page2>; rel="next"'}
            elif call_count[0] == 2:
                resp.json.return_value = [{"name": "b"}]
                resp.headers = {}
            else:
                resp.json.return_value = []
                resp.headers = {}
            return resp

        mock_requests.get.side_effect = mock_get

        api_config = self._make_api_config(
            pagination={"type": "link", "page_size": 1, "max_rows": 100,
                        "link_path": "nonexistent_field"},
        )
        result = svc._execute_api_generator(api_config)
        assert len(result) == 2

    @patch("app.core.arricchisci.api_connector.requests")
    def test_unknown_pagination_type_falls_back_to_single_call(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "Italy"}]
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config(
            pagination={"type": "unknown_type"},
        )
        result = svc._execute_api_generator(api_config)
        assert len(result) == 1

    @patch("app.core.arricchisci.api_connector.requests")
    def test_empty_result_returns_empty_dataframe(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_requests.get.return_value = mock_resp

        api_config = self._make_api_config(output_mapping=[])
        result = svc._execute_api_generator(api_config)
        assert isinstance(result, pd.DataFrame)

    @patch("app.core.arricchisci.api_connector.requests")
    def test_path_params_in_generator_url(self, mock_requests):
        svc = _make_svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "Italy"}]
        mock_requests.get.return_value = mock_resp
        # requests.utils.quote must return a real string for URL replacement
        mock_requests.utils.quote.side_effect = lambda s, safe='': str(s)

        input_df = pd.DataFrame({"country": ["Italy"]})
        api_config = self._make_api_config()
        api_config["endpoint"]["url"] = "http://example.com/api/{country}"
        svc._execute_api_generator(api_config, input_df)
        call_url = mock_requests.get.call_args[0][0]
        assert "Italy" in call_url

    @patch("app.core.arricchisci.api_connector.requests")
    def test_offset_pagination_partial_last_page(self, mock_requests):
        """When last page returns fewer rows than page_size, pagination stops."""
        svc = _make_svc()
        call_count = [0]

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count[0] == 1:
                resp.json.return_value = [{"name": "a"}, {"name": "b"}]
            else:
                resp.json.return_value = [{"name": "c"}]  # partial page
            return resp

        mock_requests.get.side_effect = mock_get

        api_config = self._make_api_config(
            pagination={"type": "offset", "page_size": 2, "max_rows": 100},
        )
        result = svc._execute_api_generator(api_config)
        assert len(result) == 3
        # Should have stopped after 2 calls (page 2 had fewer than page_size)
        assert call_count[0] == 2


# =============================================================================
# _extract_rows_from_response — wildcard non-list (line 1428)
# =============================================================================

class TestExtractRowsWildcardNonList:

    def test_wildcard_path_with_non_list_wraps_in_list(self, svc):
        """Line 1428: when wildcard root is not a list, wrap in a list."""
        data = {"data": {"id": 1, "name": "single"}}
        mapping = [
            {"path": "data[*].id", "column": "id"},
            {"path": "data[*].name", "column": "name"},
        ]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["name"] == "single"

    def test_wildcard_path_with_none_returns_empty(self, svc):
        """Line 1428: when wildcard root is None, return empty."""
        data = {"other": "stuff"}
        mapping = [{"path": "data[*].id", "column": "id"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert rows == []

    def test_wildcard_mixed_with_non_wildcard(self, svc):
        """Line 1439: non-wildcard path extracts from root data."""
        data = {"meta": "info", "items": [{"id": 1}, {"id": 2}]}
        mapping = [
            {"path": "items[*].id", "column": "id"},
            {"path": "meta", "column": "meta_val"},
        ]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 2
        assert rows[0]["meta_val"] == "info"
        assert rows[1]["meta_val"] == "info"

    def test_wildcard_empty_relative_path(self, svc):
        """When wildcard path has nothing after [*], the item itself is the value."""
        data = {"tags": ["red", "blue", "green"]}
        mapping = [{"path": "tags[*]", "column": "tag"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 3
        assert rows[0]["tag"] == "red"
        assert rows[2]["tag"] == "green"


# =============================================================================
# _extract_rows_from_response — FeatureCollection (lines 1451-1452)
# =============================================================================

class TestExtractRowsFeatureCollection:

    def test_feature_collection_path_stripping(self, svc):
        """Lines 1451-1452: features[].properties.name -> properties.name relative to feature."""
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "Roma"}, "geometry": None},
                {"type": "Feature", "properties": {"name": "Milano"}, "geometry": None},
            ],
        }
        mapping = [{"path": "features[].properties.name", "column": "city"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 2
        assert rows[0]["city"] == "Roma"
        assert rows[1]["city"] == "Milano"

    def test_feature_collection_path_without_features_prefix(self, svc):
        """Path that doesn't start with 'features[' is used directly."""
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "Roma"}, "geometry": None},
            ],
        }
        mapping = [{"path": "properties.name", "column": "city"}]
        rows = svc._extract_rows_from_response(data, mapping)
        assert len(rows) == 1
        assert rows[0]["city"] == "Roma"
