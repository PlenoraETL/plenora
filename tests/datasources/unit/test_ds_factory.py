"""
Unit tests for datasources/factory.py — create_backend, supported_types.
"""

import pytest

pytestmark = pytest.mark.unit

from app.core.datasources.factory import create_backend, supported_types
from app.core.datasources.local import LocalDataSource
from app.core.datasources.ftp import FTPDataSource
from app.core.datasources.sftp import SFTPDataSource
from app.core.datasources.sharepoint import SharePointDataSource
from app.core.datasources.interface import BackendConfig


class TestCreateBackend:

    def test_local(self):
        cfg = {"id": "d1", "type": "local", "root_path": "/tmp"}
        backend = create_backend(cfg)
        assert isinstance(backend, LocalDataSource)
        assert backend.config.type == "local"

    def test_ftp(self):
        cfg = {"id": "d2", "type": "ftp", "root_path": "/", "host": "h", "port": 21}
        backend = create_backend(cfg)
        assert isinstance(backend, FTPDataSource)
        assert backend.config.extra["host"] == "h"

    def test_sftp(self):
        cfg = {"id": "d3", "type": "sftp", "root_path": "/", "host": "h", "port": 22}
        backend = create_backend(cfg)
        assert isinstance(backend, SFTPDataSource)

    def test_sharepoint(self):
        cfg = {
            "id": "d4", "type": "sharepoint", "root_path": "Docs",
            "site_url": "https://x.sharepoint.com/sites/y",
            "client_id": "c", "client_secret": "s",
        }
        backend = create_backend(cfg)
        assert isinstance(backend, SharePointDataSource)
        assert backend.config.extra["site_url"] == "https://x.sharepoint.com/sites/y"

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="non supportato"):
            create_backend({"id": "x", "type": "s3", "root_path": "/"})

    def test_empty_type(self):
        with pytest.raises(ValueError):
            create_backend({"id": "x", "root_path": "/"})

    def test_extra_excludes_structured_keys(self):
        cfg = {
            "id": "d5", "type": "local", "root_path": "/",
            "name": "n", "description": "d",
            "created_at": "2025-01-01", "updated_at": "2025-01-01",
            "custom_field": "val",
        }
        backend = create_backend(cfg)
        assert "name" not in backend.config.extra
        assert "created_at" not in backend.config.extra
        assert backend.config.extra["custom_field"] == "val"

    def test_none_values_excluded_from_extra(self):
        cfg = {"id": "d6", "type": "local", "root_path": "/", "host": None}
        backend = create_backend(cfg)
        assert "host" not in backend.config.extra

    def test_config_root_path(self):
        cfg = {"id": "d7", "type": "local", "root_path": "/my/root"}
        backend = create_backend(cfg)
        assert backend.config.root_path == "/my/root"

    def test_missing_root_path_defaults_empty(self):
        cfg = {"id": "d8", "type": "local"}
        backend = create_backend(cfg)
        assert backend.config.root_path == ""


class TestSupportedTypes:

    def test_returns_list(self):
        result = supported_types()
        assert isinstance(result, list)

    def test_contains_all_four(self):
        result = supported_types()
        assert set(result) == {"local", "ftp", "sftp", "sharepoint"}
