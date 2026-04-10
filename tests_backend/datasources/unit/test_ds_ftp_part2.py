"""
Unit tests for datasources/ftp.py — file operations, helpers.
"""

import io
import pytest
import ftplib
from unittest.mock import MagicMock, patch, call
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
)
from app.core.datasources.ftp import FTPDataSource


def _ds():
    cfg = BackendConfig(id="t", type="ftp", root_path="/data", extra={
        "host": "h", "username": "u", "password": "p",
    })
    ds = FTPDataSource(cfg)
    ds._ftp = MagicMock()
    return ds


# ========================== list_files (MLSD) =============================

class TestListFilesMLSD:

    def test_mlsd_success(self):
        ds = _ds()
        ds._ftp.mlsd.return_value = [
            ("report.csv", {"type": "file", "size": "1024", "modify": "20250115103000"}),
            ("subdir", {"type": "dir", "size": "0", "modify": "20250101000000"}),
            ("data.txt", {"type": "file", "size": "512", "modify": "20250110080000"}),
        ]
        files = ds.list_files(path="exports")
        assert len(files) == 2  # dirs filtered out
        assert files[0].name == "report.csv"  # most recent first
        assert files[0].size == 1024

    def test_mlsd_with_pattern(self):
        ds = _ds()
        ds._ftp.mlsd.return_value = [
            ("a.csv", {"type": "file", "size": "10", "modify": "20250101000000"}),
            ("b.txt", {"type": "file", "size": "20", "modify": "20250101000000"}),
        ]
        files = ds.list_files(pattern="*.csv")
        assert len(files) == 1
        assert files[0].name == "a.csv"


# ========================== list_files (NLST fallback) ====================

class TestListFilesNLST:

    def test_nlst_fallback(self):
        ds = _ds()
        ds._ftp.mlsd.side_effect = ftplib.error_perm("500 Unknown command")
        ds._ftp.nlst.return_value = ["dir/a.csv", "dir/b.txt"]
        ds._ftp.size.return_value = 100
        ds._ftp.sendcmd.return_value = "213 20250115103000"
        files = ds.list_files(path="dir")
        assert len(files) == 2

    def test_nlst_path_not_found(self):
        ds = _ds()
        ds._ftp.mlsd.side_effect = ftplib.error_perm("500")
        ds._ftp.nlst.side_effect = ftplib.error_perm("550 No such dir")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.list_files(path="nope")

    def test_nlst_skips_on_error(self):
        ds = _ds()
        ds._ftp.mlsd.side_effect = ftplib.error_perm("500")
        ds._ftp.nlst.return_value = ["dir/a.csv", "dir/b.txt"]
        ds._ftp.size.side_effect = OSError("err")
        files = ds.list_files(path="dir")
        assert len(files) == 0  # all skipped due to errors


# ========================== file_info =====================================

class TestFileInfo:

    def test_success(self):
        ds = _ds()
        ds._ftp.size.return_value = 2048
        ds._ftp.sendcmd.return_value = "213 20250301120000"
        fi = ds.file_info("exports/data.csv")
        assert fi.name == "data.csv"
        assert fi.size == 2048
        assert fi.type == "csv"
        assert fi.modified_date == datetime(2025, 3, 1, 12, 0, 0)

    def test_size_none(self):
        ds = _ds()
        ds._ftp.size.return_value = None
        with pytest.raises(DataSourceFileNotFoundError):
            ds.file_info("nope.txt")

    def test_error_perm(self):
        ds = _ds()
        ds._ftp.size.side_effect = ftplib.error_perm("550")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.file_info("nope.txt")

    def test_connection_error(self):
        ds = _ds()
        ds._ftp.size.side_effect = OSError("network")
        with pytest.raises(DataSourceConnectionError):
            ds.file_info("x.txt")


# ========================== read_file =====================================

class TestReadFile:

    def test_success(self):
        ds = _ds()
        content = b"file-content"

        def fake_retr(cmd, callback):
            callback(content)

        ds._ftp.retrbinary = fake_retr
        result = ds.read_file("data.csv")
        assert result == content

    def test_not_found_550(self):
        ds = _ds()
        ds._ftp.retrbinary.side_effect = ftplib.error_perm("550 No such file")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.read_file("nope.txt")

    def test_permission_error(self):
        ds = _ds()
        ds._ftp.retrbinary.side_effect = ftplib.error_perm("553 Permission denied")
        with pytest.raises(DataSourcePermissionError):
            ds.read_file("locked.txt")

    def test_connection_error(self):
        ds = _ds()
        ds._ftp.retrbinary.side_effect = OSError("network")
        with pytest.raises(DataSourceConnectionError):
            ds.read_file("x.txt")


# ========================== write_file ====================================

class TestWriteFile:

    def test_success(self):
        ds = _ds()
        ds.write_file("new.txt", b"content")
        ds._ftp.storbinary.assert_called_once()

    def test_creates_parent_dirs(self):
        ds = _ds()
        ds.write_file("sub/deep/file.txt", b"x")
        assert ds._ftp.mkd.called  # _mkdirs called for parent

    def test_permission_error(self):
        ds = _ds()
        ds._ftp.storbinary.side_effect = ftplib.error_perm("553")
        with pytest.raises(DataSourcePermissionError):
            ds.write_file("f.txt", b"x")

    def test_connection_error(self):
        ds = _ds()
        ds._ftp.storbinary.side_effect = OSError("net")
        with pytest.raises(DataSourceConnectionError):
            ds.write_file("f.txt", b"x")


# ========================== delete_file ===================================

class TestDeleteFile:

    def test_success(self):
        ds = _ds()
        ds.delete_file("old.txt")
        ds._ftp.delete.assert_called_once_with("old.txt")

    def test_not_found(self):
        ds = _ds()
        ds._ftp.delete.side_effect = ftplib.error_perm("550 No such file")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.delete_file("nope.txt")

    def test_permission_denied(self):
        ds = _ds()
        ds._ftp.delete.side_effect = ftplib.error_perm("553")
        with pytest.raises(DataSourcePermissionError):
            ds.delete_file("locked.txt")

    def test_connection_error(self):
        ds = _ds()
        ds._ftp.delete.side_effect = OSError("net")
        with pytest.raises(DataSourceConnectionError):
            ds.delete_file("x.txt")


# ========================== file_exists ===================================

class TestFileExists:

    def test_exists(self):
        ds = _ds()
        ds._ftp.size.return_value = 100
        assert ds.file_exists("data.csv") is True

    def test_not_exists(self):
        ds = _ds()
        ds._ftp.size.side_effect = OSError("550")
        assert ds.file_exists("nope.csv") is False

    def test_size_none(self):
        ds = _ds()
        ds._ftp.size.return_value = None
        # None means "exists but size unknown" — code returns `is not None`
        assert ds.file_exists("x") is False


# ========================== _parse_mlsd_time ==============================

class TestParseMlsdTime:

    def test_valid(self):
        dt = FTPDataSource._parse_mlsd_time("20250115103045")
        assert dt == datetime(2025, 1, 15, 10, 30, 45)

    def test_with_fractional(self):
        dt = FTPDataSource._parse_mlsd_time("20250115103045.123")
        assert dt == datetime(2025, 1, 15, 10, 30, 45)

    def test_empty(self):
        dt = FTPDataSource._parse_mlsd_time("")
        assert dt == datetime(1970, 1, 1)

    def test_invalid(self):
        dt = FTPDataSource._parse_mlsd_time("not-a-date")
        assert dt == datetime(1970, 1, 1)


# ========================== _parse_mdtm ===================================

class TestParseMdtm:

    def test_valid(self):
        dt = FTPDataSource._parse_mdtm("213 20250301120000")
        assert dt == datetime(2025, 3, 1, 12, 0, 0)

    def test_invalid_format(self):
        dt = FTPDataSource._parse_mdtm("bad response")
        assert dt == datetime(1970, 1, 1)

    def test_no_space(self):
        dt = FTPDataSource._parse_mdtm("213")
        # IndexError on split → fallback
        assert dt == datetime(1970, 1, 1)


# ========================== _mkdirs =======================================

class TestMkdirs:

    def test_creates_dirs(self):
        ds = _ds()
        ds._mkdirs("a/b/c")
        calls = ds._ftp.mkd.call_args_list
        assert call("a") in calls
        assert call("a/b") in calls
        assert call("a/b/c") in calls

    def test_ignores_existing(self):
        ds = _ds()
        ds._ftp.mkd.side_effect = ftplib.error_perm("exists")
        ds._mkdirs("a/b")  # no exception


# ========================== _maybe_hash ===================================

class TestMaybeHash:

    def test_false_returns_none(self):
        ds = _ds()
        assert ds._maybe_hash("x.txt", False) is None

    def test_true_success(self):
        ds = _ds()
        content = b"test"

        def fake_retr(cmd, callback):
            callback(content)

        ds._ftp.retrbinary = fake_retr
        result = ds._maybe_hash("x.txt", True)
        assert result is not None

    def test_true_error_returns_none(self):
        ds = _ds()
        ds._ftp.retrbinary.side_effect = Exception("err")
        assert ds._maybe_hash("x.txt", True) is None
