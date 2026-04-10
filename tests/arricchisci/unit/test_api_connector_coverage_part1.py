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
# list_api_connections  (lines 132-148)
# =============================================================================

class TestListApiConnections:

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_returns_sorted_by_created_at_desc(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_a", "name": "A", "created_at": "2026-01-01T00:00:00"}},
            {"config": {"id": "api_b", "name": "B", "created_at": "2026-02-01T00:00:00"}},
        ]
        result = svc.list_api_connections()
        assert len(result) == 2
        # Most recent first
        assert result[0]["name"] == "B"
        assert result[1]["name"] == "A"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_filters_by_category(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_a", "category": "custom", "created_at": "2026-01-01"}},
            {"config": {"id": "api_b", "category": "geocoding", "created_at": "2026-01-02"}},
        ]
        result = svc.list_api_connections(category="geocoding")
        assert len(result) == 1
        assert result[0]["id"] == "api_b"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_filters_by_connection_type(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_a", "connection_type": "manual", "created_at": "2026-01-01"}},
            {"config": {"id": "api_b", "connection_type": "template", "created_at": "2026-01-02"}},
        ]
        result = svc.list_api_connections(connection_type="template")
        assert len(result) == 1
        assert result[0]["id"] == "api_b"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_skips_datasource_category(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_a", "category": "datasource", "created_at": "2026-01-01"}},
            {"config": {"id": "api_b", "category": "custom", "created_at": "2026-01-02"}},
        ]
        result = svc.list_api_connections()
        assert len(result) == 1
        assert result[0]["id"] == "api_b"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_defaults_connection_type_to_manual(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        # Config without connection_type
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_a", "created_at": "2026-01-01"}},
        ]
        result = svc.list_api_connections()
        assert result[0]["connection_type"] == "manual"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_parses_string_config(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": json.dumps({"id": "api_a", "created_at": "2026-01-01"})},
        ]
        result = svc.list_api_connections()
        assert result[0]["id"] == "api_a"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_masks_credentials(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = [
            {"config": {"id": "api_a", "auth": {"token": "secret123"}, "created_at": "2026-01-01"}},
        ]
        result = svc.list_api_connections()
        assert result[0]["auth"]["token"] == "***"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_list_uses_group_id_param(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        mock_db.list_api_connections.return_value = []
        svc.list_api_connections(group_id="custom_group")
        mock_db.list_api_connections.assert_called_once_with("custom_group")


# =============================================================================
# get_api_connection  (lines 153-162)
# =============================================================================

class TestGetApiConnection:

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_get_returns_none_when_not_found(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = None
        assert svc.get_api_connection("nonexistent") is None

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_get_masks_credentials_by_default(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x", "auth": {"token": "secret"}},
            "group_id": "grp1",
        }
        result = svc.get_api_connection("api_x")
        assert result["auth"]["token"] == "***"
        assert result["group_id"] == "grp1"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_get_includes_token_when_requested(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x", "auth": {"token": "secret"}},
            "group_id": "grp1",
        }
        result = svc.get_api_connection("api_x", include_token=True)
        assert result["auth"]["token"] == "secret"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_get_parses_string_config(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": json.dumps({"id": "api_x", "auth": {"type": "none"}}),
            "group_id": "grp1",
        }
        result = svc.get_api_connection("api_x")
        assert result["id"] == "api_x"


# =============================================================================
# create_api_connection  (lines 166-204)
# =============================================================================

class TestCreateApiConnection:

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_create_generates_api_id(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        result = svc.create_api_connection({"name": "Test"})
        assert result["id"].startswith("api_")
        assert len(result["id"]) == 16  # "api_" + 12 hex chars

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_create_uses_defaults(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        result = svc.create_api_connection({})
        assert result["name"] == "Nuova API"
        assert result["description"] == ""
        assert result["api_type"] == "enricher"
        assert result["category"] == "custom"
        assert result["connection_type"] == "manual"
        assert result["endpoint"]["url"] == ""
        assert result["endpoint"]["method"] == "GET"
        assert result["auth"] == {"type": "none"}
        assert result["custom_headers"] == {}
        assert result["parameters"] == []
        assert result["static_parameters"] == {}
        assert result["output_mapping"] == []

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_create_uses_exec_type_fallback(self, mock_settings, mock_db):
        """api_type falls back to exec_type for backward compatibility."""
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        result = svc.create_api_connection({"exec_type": "generator"})
        assert result["api_type"] == "generator"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_create_includes_optional_fields(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        result = svc.create_api_connection({
            "executable": {"type": "python", "path": "/script.py"},
            "response_config": {"mappings": []},
            "pagination": {"type": "offset", "page_size": 50},
            "batch": {"size": 10},
        })
        assert result["executable"]["type"] == "python"
        assert result["response_config"] == {"mappings": []}
        assert result["pagination"]["type"] == "offset"
        assert result["batch"]["size"] == 10

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_create_saves_to_db(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        svc.create_api_connection({"name": "Test", "group_id": "mygroup"})
        mock_db.save_api_connection.assert_called_once()
        call_kwargs = mock_db.save_api_connection.call_args
        assert call_kwargs[1]["group_id"] == "mygroup"

    @patch("app.core.arricchisci.api_connector.db_svc")
    @patch("app.core.arricchisci.api_connector.settings")
    def test_create_has_timestamps(self, mock_settings, mock_db):
        mock_settings.DEFAULT_GROUP = "grp1"
        svc = _make_svc()
        result = svc.create_api_connection({"name": "Test"})
        assert "created_at" in result
        assert "updated_at" in result


# =============================================================================
# update_api_connection  (lines 208-234)
# =============================================================================

class TestUpdateApiConnection:

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_returns_none_when_not_found(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = None
        assert svc.update_api_connection("nonexistent", {"name": "X"}) is None

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_modifies_updatable_fields(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x", "name": "Old", "description": "Old desc"},
        }
        result = svc.update_api_connection("api_x", {"name": "New", "description": "New desc"})
        assert result["name"] == "New"
        assert result["description"] == "New desc"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_url_sets_endpoint(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x"},
        }
        result = svc.update_api_connection("api_x", {"url": "http://new.api.com"})
        assert result["endpoint"]["url"] == "http://new.api.com"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_method_sets_endpoint(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x"},
        }
        result = svc.update_api_connection("api_x", {"method": "POST"})
        assert result["endpoint"]["method"] == "POST"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_auth_merges_not_replaces(self, mock_db):
        """B-BE-22: auth merge preserves unmodified fields (e.g. masked password)."""
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x", "auth": {"type": "basic", "username": "user", "password": "secret"}},
        }
        result = svc.update_api_connection("api_x", {"auth": {"type": "basic", "username": "newuser"}})
        assert result["auth"]["username"] == "newuser"
        assert result["auth"]["password"] == "secret"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_auth_none_treated_as_empty(self, mock_db):
        """When auth is None, treat as empty dict to avoid merge crash."""
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x", "auth": {"type": "basic", "password": "old"}},
        }
        result = svc.update_api_connection("api_x", {"auth": None})
        # None is treated as {}, so existing auth is preserved
        assert result["auth"]["password"] == "old"

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_sets_updated_at(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": {"id": "api_x"},
        }
        result = svc.update_api_connection("api_x", {"name": "X"})
        assert "updated_at" in result

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_update_parses_string_config(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {
            "config": json.dumps({"id": "api_x", "name": "Old"}),
        }
        result = svc.update_api_connection("api_x", {"name": "New"})
        assert result["name"] == "New"


# =============================================================================
# delete_api_connection  (lines 238-242)
# =============================================================================

class TestDeleteApiConnection:

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_delete_returns_false_when_not_found(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = None
        assert svc.delete_api_connection("nonexistent") is False

    @patch("app.core.arricchisci.api_connector.db_svc")
    def test_delete_returns_true_on_success(self, mock_db):
        svc = _make_svc()
        mock_db.get_api_connection.return_value = {"config": {"id": "api_x"}}
        assert svc.delete_api_connection("api_x") is True
        mock_db.delete_api_connection.assert_called_once_with("api_x")
