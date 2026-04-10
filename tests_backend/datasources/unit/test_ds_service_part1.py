"""
Unit tests for datasources/service.py — CRUD operations, helpers.
All DB calls are mocked.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.datasources.models import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceList,
    DataSourceTestResult,
)
from app.core.datasources.service import DataSourceService


@pytest.fixture
def svc(tmp_path):
    """DataSourceService with scan_dir pointing to tmp_path."""
    s = DataSourceService.__new__(DataSourceService)
    s._scan_dir = tmp_path / "scan"
    return s


def _mock_db_row(ds_id="ds_test", ds_type="local", config=None):
    if config is None:
        config = {
            "id": ds_id,
            "name": "Test",
            "type": ds_type,
            "root_path": "/data",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
    return {"id": ds_id, "group_id": "g1", "config": config}


# ========================== list_datasources ==============================

class TestListDatasources:

    @patch("app.core.datasources.service.settings")
    def test_returns_list(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        with patch("app.db.db_service.list_datasources") as mock_list, \
             patch("app.db.db_service.get_datasource") as mock_get:
            mock_list.return_value = [{"id": "ds1"}, {"id": "ds2"}]
            mock_get.side_effect = [
                _mock_db_row("ds1"),
                _mock_db_row("ds2"),
            ]
            result = svc.list_datasources()
            assert isinstance(result, DataSourceList)
            assert len(result.datasources) == 2

    @patch("app.core.datasources.service.settings")
    def test_empty(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        with patch("app.db.db_service.list_datasources") as mock_list:
            mock_list.return_value = []
            result = svc.list_datasources()
            assert len(result.datasources) == 0

    @patch("app.core.datasources.service.settings")
    def test_skips_missing_config(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        with patch("app.db.db_service.list_datasources") as mock_list, \
             patch("app.db.db_service.get_datasource") as mock_get:
            mock_list.return_value = [{"id": "ds1"}]
            mock_get.return_value = None
            result = svc.list_datasources()
            assert len(result.datasources) == 0


# ========================== get_datasource ================================

class TestGetDatasource:

    def test_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = _mock_db_row()
            result = svc.get_datasource("ds_test")
            assert isinstance(result, DataSource)
            assert result.id == "ds_test"

    def test_not_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            result = svc.get_datasource("nope")
            assert result is None


# ========================== create_datasource =============================

class TestCreateDatasource:

    @patch("app.core.datasources.service.settings")
    def test_success(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        data = DataSourceCreate(name="MyDS", type="local", root_path="/tmp")
        with patch("app.db.db_service.save_datasource") as mock_save:
            result = svc.create_datasource(data)
            assert isinstance(result, DataSource)
            assert result.name == "MyDS"
            assert result.type == "local"
            mock_save.assert_called_once()

    @patch("app.core.datasources.service.settings")
    def test_with_group_id(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        data = DataSourceCreate(name="DS", type="ftp", host="h")
        with patch("app.db.db_service.save_datasource") as mock_save:
            svc.create_datasource(data, group_id="custom_group")
            call_kwargs = mock_save.call_args[1]
            assert call_kwargs["group_id"] == "custom_group"

    @patch("app.core.datasources.service.settings")
    def test_sensitive_fields_redacted(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        data = DataSourceCreate(
            name="DS", type="ftp", host="h",
            password="secret", username="user",
        )
        with patch("app.db.db_service.save_datasource"):
            result = svc.create_datasource(data)
            assert result.password == "***"

    @patch("app.core.datasources.service.settings")
    def test_none_values_stripped(self, mock_settings, svc):
        mock_settings.DEFAULT_GROUP = "default"
        data = DataSourceCreate(name="DS", type="local")
        with patch("app.db.db_service.save_datasource") as mock_save:
            svc.create_datasource(data)
            config = mock_save.call_args[1]["config"]
            assert "host" not in config


# ========================== update_datasource =============================

class TestUpdateDatasource:

    def test_success(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get, \
             patch("app.db.db_service.update_datasource") as mock_update:
            mock_get.return_value = _mock_db_row()
            data = DataSourceUpdate(name="NewName")
            result = svc.update_datasource("ds_test", data)
            assert result.name == "NewName"
            mock_update.assert_called_once()

    def test_not_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            data = DataSourceUpdate(name="X")
            result = svc.update_datasource("nope", data)
            assert result is None


# ========================== delete_datasource =============================

class TestDeleteDatasource:

    def test_success(self, svc, tmp_path):
        scan_file = svc._scan_dir / "ds_test.json"
        scan_file.parent.mkdir(parents=True, exist_ok=True)
        scan_file.write_text("{}")
        with patch("app.db.db_service.get_datasource") as mock_get, \
             patch("app.db.db_service.delete_datasource") as mock_del:
            mock_get.return_value = _mock_db_row()
            result = svc.delete_datasource("ds_test")
            assert result is True
            mock_del.assert_called_once_with("ds_test")
            assert not scan_file.exists()

    def test_not_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            result = svc.delete_datasource("nope")
            assert result is False

    def test_no_scan_file(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get, \
             patch("app.db.db_service.delete_datasource"):
            mock_get.return_value = _mock_db_row()
            result = svc.delete_datasource("ds_test")
            assert result is True


# ========================== _generate_id ==================================

class TestGenerateId:

    def test_format(self, svc):
        result = svc._generate_id("My Data Source")
        assert result.startswith("ds_My_Data_Source_")
        parts = result.split("_")
        assert len(parts) >= 5  # ds + name parts + timestamp + uuid

    def test_special_chars_stripped(self, svc):
        result = svc._generate_id("test@#$%name!")
        assert "@" not in result
        assert "#" not in result

    def test_long_name_truncated(self, svc):
        result = svc._generate_id("a" * 100)
        # Name portion should be max 30 chars
        name_part = result.split("_", 1)[1].rsplit("_", 3)[0]
        assert len(name_part) <= 30


# ========================== _to_model =====================================

class TestToModel:

    def test_redaction(self, svc):
        data = {
            "id": "ds1", "name": "n", "type": "ftp",
            "password": "secret", "private_key": "pem",
            "client_secret": "cs", "private_key_passphrase": "pp",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
        model = svc._to_model(data, redact=True)
        assert model.password == "***"
        assert model.private_key == "***"
        assert model.client_secret == "***"

    def test_no_redaction(self, svc):
        data = {
            "id": "ds1", "name": "n", "type": "ftp",
            "password": "secret",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
        model = svc._to_model(data, redact=False)
        assert model.password == "secret"

    def test_timestamp_parsing(self, svc):
        data = {
            "id": "ds1", "name": "n", "type": "local",
            "created_at": "2025-06-15T10:30:00",
            "updated_at": "2025-06-15T10:30:00",
        }
        model = svc._to_model(data)
        assert isinstance(model.created_at, datetime)

    def test_invalid_timestamp_fallback(self, svc):
        data = {
            "id": "ds1", "name": "n", "type": "local",
            "created_at": "bad-date",
            "updated_at": "bad-date",
        }
        model = svc._to_model(data)
        assert isinstance(model.created_at, datetime)

    def test_extra_keys_ignored(self, svc):
        data = {
            "id": "ds1", "name": "n", "type": "local",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
            "unknown_field": "val",
        }
        model = svc._to_model(data)
        assert model.id == "ds1"


# ========================== _get_full_config ==============================

class TestGetFullConfig:

    def test_success(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": {"id": "ds1", "type": "local", "root_path": "/"},
            }
            result = svc._get_full_config("ds1")
            assert result["group_id"] == "g1"
            assert result["type"] == "local"

    def test_config_as_string(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = {
                "id": "ds1", "group_id": "g1",
                "config": json.dumps({"id": "ds1", "type": "local"}),
            }
            result = svc._get_full_config("ds1")
            assert result["type"] == "local"

    def test_not_found(self, svc):
        with patch("app.db.db_service.get_datasource") as mock_get:
            mock_get.return_value = None
            result = svc._get_full_config("nope")
            assert result is None
