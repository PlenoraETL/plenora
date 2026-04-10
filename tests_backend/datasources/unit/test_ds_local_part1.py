"""
Unit tests for datasources/local.py — connect, disconnect, test_connection, list_files.
Uses tmp_path for real filesystem operations.
"""

import os
import pytest
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
)
from app.core.datasources.local import LocalDataSource


def _make_ds(root_path: str) -> LocalDataSource:
    cfg = BackendConfig(id="test", type="local", root_path=root_path)
    return LocalDataSource(cfg)


# ========================== connect =======================================

class TestConnect:

    def test_success(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        assert ds._connected is True

    def test_not_exists(self, tmp_path):
        ds = _make_ds(str(tmp_path / "nonexistent"))
        with pytest.raises(DataSourceConnectionError, match="non trovata"):
            ds.connect()

    def test_no_read_permission(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "access", lambda p, m: False)
        ds = _make_ds(str(tmp_path))
        with pytest.raises(DataSourceConnectionError, match="Permessi"):
            ds.connect()


# ========================== disconnect ====================================

class TestDisconnect:

    def test_disconnect(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        ds.disconnect()
        assert ds._connected is False

    def test_disconnect_already_disconnected(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.disconnect()  # no error
        assert ds._connected is False


# ========================== test_connection ===============================

class TestTestConnection:

    def test_success(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi")
        (tmp_path / "b.csv").write_text("x")
        ds = _make_ds(str(tmp_path))
        result = ds.test_connection()
        assert result["ok"] is True
        assert result["details"]["files_in_root"] == 2
        assert ds._connected is False  # disconnected after test

    def test_failure(self, tmp_path):
        ds = _make_ds(str(tmp_path / "missing"))
        result = ds.test_connection()
        assert result["ok"] is False

    def test_permission_error_on_listdir(self, tmp_path, monkeypatch):
        ds = _make_ds(str(tmp_path))
        original_listdir = os.listdir
        def _raise_perm(p):
            raise PermissionError("no")
        monkeypatch.setattr(os, "listdir", _raise_perm)
        result = ds.test_connection()
        assert result["ok"] is True
        assert result["details"]["files_in_root"] is None


# ========================== list_files ====================================

class TestListFiles:

    def _setup_tree(self, tmp_path):
        """Create a small tree for testing."""
        (tmp_path / "a.csv").write_text("1")
        (tmp_path / "b.txt").write_text("2")
        (tmp_path / ".hidden").write_text("3")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "c.csv").write_text("4")
        return _make_ds(str(tmp_path))

    def test_non_recursive_lists_files_and_folders(self, tmp_path):
        ds = self._setup_tree(tmp_path)
        ds.connect()
        files = ds.list_files()
        names = {f.name for f in files}
        assert "a.csv" in names
        assert "b.txt" in names
        assert "subdir" in names
        assert ".hidden" not in names  # hidden excluded

    def test_non_recursive_pattern(self, tmp_path):
        ds = self._setup_tree(tmp_path)
        ds.connect()
        files = ds.list_files(pattern="*.csv")
        file_names = {f.name for f in files if f.type != "folder"}
        assert file_names == {"a.csv"}
        # folders are still listed (pattern applies to files only)
        folder_names = {f.name for f in files if f.type == "folder"}
        assert "subdir" in folder_names

    def test_recursive(self, tmp_path):
        ds = self._setup_tree(tmp_path)
        ds.connect()
        files = ds.list_files(recursive=True)
        names = {f.name for f in files}
        assert "a.csv" in names
        assert "c.csv" in names
        # recursive mode lists files only (not folders as entries)
        types = {f.type for f in files}
        assert "folder" not in types

    def test_recursive_pattern(self, tmp_path):
        ds = self._setup_tree(tmp_path)
        ds.connect()
        files = ds.list_files(pattern="*.csv", recursive=True)
        names = {f.name for f in files}
        assert names == {"a.csv", "c.csv"}

    def test_subpath(self, tmp_path):
        ds = self._setup_tree(tmp_path)
        ds.connect()
        files = ds.list_files(path="subdir")
        names = {f.name for f in files}
        assert "c.csv" in names

    def test_nonexistent_path(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        with pytest.raises(DataSourceFileNotFoundError):
            ds.list_files(path="nope")

    def test_folders_come_first(self, tmp_path):
        (tmp_path / "z.txt").write_text("z")
        sub = tmp_path / "aaa_dir"
        sub.mkdir()
        (sub / "x.txt").write_text("x")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        files = ds.list_files()
        assert files[0].type == "folder"

    def test_include_hash(self, tmp_path):
        content = b"test content"
        (tmp_path / "data.bin").write_bytes(content)
        ds = _make_ds(str(tmp_path))
        ds.connect()
        files = ds.list_files(include_hash=True)
        data_file = [f for f in files if f.name == "data.bin"][0]
        assert data_file.hash is not None
        import hashlib
        assert data_file.hash == hashlib.md5(content).hexdigest()

    def test_file_info_fields(self, tmp_path):
        (tmp_path / "x.csv").write_text("abc")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        files = ds.list_files()
        f = [x for x in files if x.name == "x.csv"][0]
        assert f.type == "csv"
        assert f.size > 0
        assert isinstance(f.modified_date, datetime)
        assert f.path is not None
