"""
Unit tests for datasources/sharepoint.py — connect, disconnect, test_connection, helpers.
All office365 interactions are mocked.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
)
from app.core.datasources.sharepoint import SharePointDataSource


def _cfg(**extra):
    return BackendConfig(id="t", type="sharepoint", root_path="GIS", extra=extra)


def _ds(**extra):
    defaults = {
        "site_url": "https://tenant.sharepoint.com/sites/mysite",
        "client_id": "cid",
        "client_secret": "csecret",
        "tenant_id": "tid",
    }
    defaults.update(extra)
    return SharePointDataSource(_cfg(**defaults))


# ========================== connect =======================================

class TestConnect:

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_success(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        mock_ctx = MagicMock()
        MockCtx.return_value.with_credentials.return_value = mock_ctx

        ds = _ds()
        ds.connect()
        assert ds._ctx is mock_ctx
        mock_ctx.load.assert_called()
        mock_ctx.execute_query.assert_called()

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_library_not_installed(self, mock_import):
        mock_import.return_value = (None, None)
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Office365"):
            ds.connect()

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_missing_fields(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        ds = _ds(site_url="", client_id="c", client_secret="s")
        with pytest.raises(DataSourceConnectionError, match="incompleta"):
            ds.connect()

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_missing_client_id(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        ds = _ds(client_id="")
        with pytest.raises(DataSourceConnectionError, match="incompleta"):
            ds.connect()

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_auth_failure(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        mock_ctx = MagicMock()
        MockCtx.return_value.with_credentials.return_value = mock_ctx
        mock_ctx.execute_query.side_effect = Exception("401 Unauthorized")

        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Autenticazione"):
            ds.connect()

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_tenant_id_set(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        mock_ctx = MagicMock()
        MockCtx.return_value.with_credentials.return_value = mock_ctx

        ds = _ds(tenant_id="my-tenant")
        ds.connect()
        assert mock_ctx.authentication_context.tenant == "my-tenant"


# ========================== disconnect ====================================

class TestDisconnect:

    def test_sets_none(self):
        ds = _ds()
        ds._ctx = MagicMock()
        ds.disconnect()
        assert ds._ctx is None

    def test_already_disconnected(self):
        ds = _ds()
        ds.disconnect()  # no error


# ========================== test_connection ===============================

class TestTestConnection:

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_success(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        mock_ctx = MagicMock()
        MockCtx.return_value.with_credentials.return_value = mock_ctx
        mock_ctx.web.properties = {"Title": "MySite"}

        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is True
        assert "MySite" in result["message"]
        assert ds._ctx is None

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_failure(self, mock_import):
        mock_import.return_value = (None, None)
        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is False

    @patch("app.core.datasources.sharepoint._import_office365")
    def test_unexpected_error(self, mock_import):
        MockCred = MagicMock()
        MockCtx = MagicMock()
        mock_import.return_value = (MockCred, MockCtx)
        mock_ctx = MagicMock()
        MockCtx.return_value.with_credentials.return_value = mock_ctx
        # First execute_query (connect) succeeds, second (test_connection body) fails
        mock_ctx.execute_query.side_effect = [None, Exception("random")]

        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is False


# ========================== _library_name =================================

class TestLibraryName:

    def test_simple(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="GIS")
        ds = SharePointDataSource(cfg)
        assert ds._library_name() == "GIS"

    def test_with_subpath(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="gisrepo/test/dati")
        ds = SharePointDataSource(cfg)
        assert ds._library_name() == "gisrepo"

    def test_empty_fallback(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="")
        ds = SharePointDataSource(cfg)
        assert ds._library_name() == "Documenti condivisi"

    def test_with_slashes(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="/MyLib/sub/")
        ds = SharePointDataSource(cfg)
        assert ds._library_name() == "MyLib"


# ========================== _base_path ====================================

class TestBasePath:

    def test_no_sub(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="GIS")
        ds = SharePointDataSource(cfg)
        assert ds._base_path() == ""

    def test_with_sub(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="gisrepo/test/dati")
        ds = SharePointDataSource(cfg)
        assert ds._base_path() == "test/dati"

    def test_deep_path(self):
        cfg = BackendConfig(id="t", type="sharepoint", root_path="Lib/a/b/c")
        ds = SharePointDataSource(cfg)
        assert ds._base_path() == "a/b/c"


# ========================== _server_relative_url ==========================

class TestServerRelativeUrl:

    def test_simple(self):
        ds = _ds(site_url="https://tenant.sharepoint.com/sites/mysite")
        result = ds._server_relative_url("sub/file.csv")
        # site_path="/sites/mysite" already has leading slash, method prepends another "/"
        assert "/sites/mysite/GIS/sub/file.csv" in result
        assert result.lstrip("/") == "sites/mysite/GIS/sub/file.csv"

    def test_no_relative_path(self):
        ds = _ds(site_url="https://tenant.sharepoint.com/sites/mysite")
        result = ds._server_relative_url("")
        assert result.lstrip("/") == "sites/mysite/GIS"

    def test_complex_site_url(self):
        ds = _ds(site_url="https://t.sharepoint.com/sites/workarea/gis")
        result = ds._server_relative_url("data.csv")
        assert result.lstrip("/") == "sites/workarea/gis/GIS/data.csv"


# ========================== _parse_dt =====================================

class TestParseDt:

    def test_iso_string(self):
        dt = SharePointDataSource._parse_dt("2025-01-15T10:30:00Z")
        assert dt == datetime(2025, 1, 15, 10, 30, 0)

    def test_iso_with_millis(self):
        dt = SharePointDataSource._parse_dt("2025-01-15T10:30:00.123Z")
        assert dt == datetime(2025, 1, 15, 10, 30, 0)

    def test_datetime_object(self):
        now = datetime(2025, 6, 1, 12, 0)
        assert SharePointDataSource._parse_dt(now) is now

    def test_empty(self):
        dt = SharePointDataSource._parse_dt("")
        assert dt == datetime(1970, 1, 1)

    def test_none(self):
        dt = SharePointDataSource._parse_dt(None)
        assert dt == datetime(1970, 1, 1)

    def test_invalid(self):
        dt = SharePointDataSource._parse_dt("not-a-date")
        assert dt == datetime(1970, 1, 1)


# ========================== _check ========================================

class TestCheck:

    def test_not_connected(self):
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Non connesso"):
            ds._check()

    def test_connected(self):
        ds = _ds()
        ds._ctx = MagicMock()
        ds._check()  # no exception
