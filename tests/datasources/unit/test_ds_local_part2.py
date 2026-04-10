"""
Unit tests for datasources/local.py — read_file, write_file, delete_file, file_exists, _abs, _rel.
Uses tmp_path for real filesystem operations.
"""

import os
import pytest

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


# ========================== read_file =====================================

class TestReadFile:

    def test_success(self, tmp_path):
        (tmp_path / "data.csv").write_bytes(b"col1,col2\na,b\n")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        content = ds.read_file("data.csv")
        assert content == b"col1,col2\na,b\n"

    def test_file_not_found(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        with pytest.raises(DataSourceFileNotFoundError):
            ds.read_file("nope.txt")

    def test_permission_error(self, tmp_path, monkeypatch):
        f = tmp_path / "locked.txt"
        f.write_text("x")
        ds = _make_ds(str(tmp_path))
        ds.connect()

        def _raise(*a, **kw):
            raise PermissionError("no")
        monkeypatch.setattr("builtins.open", _raise)
        with pytest.raises(DataSourcePermissionError):
            ds.read_file("locked.txt")

    def test_binary_content(self, tmp_path):
        data = bytes(range(256))
        (tmp_path / "bin.dat").write_bytes(data)
        ds = _make_ds(str(tmp_path))
        ds.connect()
        assert ds.read_file("bin.dat") == data

    def test_nested_path(self, tmp_path):
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        (sub / "deep.txt").write_bytes(b"deep")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        assert ds.read_file("a/b/deep.txt") == b"deep"


# ========================== write_file ====================================

class TestWriteFile:

    def test_create_new(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        ds.write_file("new.txt", b"hello")
        assert (tmp_path / "new.txt").read_bytes() == b"hello"

    def test_overwrite(self, tmp_path):
        (tmp_path / "exist.txt").write_text("old")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        ds.write_file("exist.txt", b"new")
        assert (tmp_path / "exist.txt").read_bytes() == b"new"

    def test_creates_directories(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        ds.write_file("sub/deep/file.txt", b"content")
        assert (tmp_path / "sub" / "deep" / "file.txt").read_bytes() == b"content"

    def test_permission_error(self, tmp_path, monkeypatch):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        original_open = open

        def _raise_perm(path, *a, **kw):
            if "forbidden" in str(path):
                raise PermissionError("no")
            return original_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", _raise_perm)
        with pytest.raises(DataSourcePermissionError):
            ds.write_file("forbidden.txt", b"x")

    def test_empty_content(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        ds.write_file("empty.bin", b"")
        assert (tmp_path / "empty.bin").read_bytes() == b""


# ========================== delete_file ===================================

class TestDeleteFile:

    def test_success(self, tmp_path):
        f = tmp_path / "del.txt"
        f.write_text("bye")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        ds.delete_file("del.txt")
        assert not f.exists()

    def test_not_found(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        with pytest.raises(DataSourceFileNotFoundError):
            ds.delete_file("nope.txt")

    def test_permission_error(self, tmp_path, monkeypatch):
        f = tmp_path / "locked.txt"
        f.write_text("x")
        ds = _make_ds(str(tmp_path))
        ds.connect()

        def _raise(p):
            raise PermissionError("no")
        monkeypatch.setattr(os, "remove", _raise)
        with pytest.raises(DataSourcePermissionError):
            ds.delete_file("locked.txt")


# ========================== file_exists ===================================

class TestFileExists:

    def test_exists(self, tmp_path):
        (tmp_path / "yes.txt").write_text("y")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        assert ds.file_exists("yes.txt") is True

    def test_not_exists(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        assert ds.file_exists("no.txt") is False

    def test_directory_returns_false(self, tmp_path):
        (tmp_path / "adir").mkdir()
        ds = _make_ds(str(tmp_path))
        ds.connect()
        assert ds.file_exists("adir") is False


# ========================== file_info =====================================

class TestFileInfo:

    def test_success(self, tmp_path):
        (tmp_path / "info.csv").write_bytes(b"abc")
        ds = _make_ds(str(tmp_path))
        ds.connect()
        fi = ds.file_info("info.csv")
        assert fi.name == "info.csv"
        assert fi.size == 3
        assert fi.type == "csv"

    def test_not_found(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        ds.connect()
        with pytest.raises(DataSourceFileNotFoundError):
            ds.file_info("nope.csv")


# ========================== _abs / _rel ===================================

class TestAbsRel:

    def test_abs_simple(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        result = ds._abs("sub/file.txt")
        expected = os.path.join(str(tmp_path), "sub", "file.txt")
        assert os.path.normpath(result) == os.path.normpath(expected)

    def test_abs_empty(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        assert ds._abs("") == str(tmp_path)

    def test_abs_strips_leading_slash(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        result = ds._abs("/sub/file.txt")
        expected = os.path.join(str(tmp_path), "sub", "file.txt")
        assert os.path.normpath(result) == os.path.normpath(expected)

    def test_rel_simple(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        abs_path = os.path.join(str(tmp_path), "sub", "file.txt")
        result = ds._rel(abs_path)
        assert result == "sub/file.txt"

    def test_rel_root(self, tmp_path):
        ds = _make_ds(str(tmp_path))
        result = ds._rel(str(tmp_path))
        assert result == "."
