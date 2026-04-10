"""
Unit tests for datasources/sftp.py — full coverage.
All paramiko interactions are mocked.
"""

import io
import stat
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

pytestmark = pytest.mark.unit

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
)
from app.core.datasources.sftp import SFTPDataSource


def _cfg(**extra):
    return BackendConfig(id="t", type="sftp", root_path="/data", extra=extra)


def _ds(**extra):
    defaults = {"host": "sftp.example.com", "port": 22, "username": "u", "password": "p"}
    defaults.update(extra)
    return SFTPDataSource(_cfg(**defaults))


def _connected_ds(**extra):
    ds = _ds(**extra)
    ds._ssh = MagicMock()
    ds._sftp = MagicMock()
    return ds


# ========================== connect =======================================

class TestConnect:

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_password_auth(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        mock_ssh = MagicMock()
        paramiko.SSHClient.return_value = mock_ssh
        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp

        ds = _ds()
        ds.connect()
        mock_ssh.connect.assert_called_once()
        kwargs = mock_ssh.connect.call_args[1]
        assert kwargs["password"] == "p"
        assert kwargs["hostname"] == "sftp.example.com"
        mock_sftp.chdir.assert_called_once_with("/data")

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_key_auth(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        mock_ssh = MagicMock()
        paramiko.SSHClient.return_value = mock_ssh
        mock_ssh.open_sftp.return_value = MagicMock()
        # Make RSAKey succeed
        paramiko.RSAKey.from_private_key.return_value = MagicMock()

        ds = _ds(private_key="PEM_DATA", password="")
        ds.connect()
        kwargs = mock_ssh.connect.call_args[1]
        assert "pkey" in kwargs
        assert "password" not in kwargs

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_reject_policy(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        mock_ssh = MagicMock()
        paramiko.SSHClient.return_value = mock_ssh
        mock_ssh.open_sftp.return_value = MagicMock()

        ds = _ds(host_key_policy="reject")
        ds.connect()
        mock_ssh.load_system_host_keys.assert_called_once()
        mock_ssh.set_missing_host_key_policy.assert_called_once()

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_missing_creds(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        paramiko.AuthenticationException = type("AuthenticationException", (Exception,), {})
        paramiko.SSHException = type("SSHException", (Exception,), {})
        paramiko.SSHClient.return_value = MagicMock()

        ds = _ds(password="", private_key="")
        with pytest.raises(DataSourceConnectionError, match="Credenziali SFTP mancanti"):
            ds.connect()

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_no_host(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        ds = _ds(host="")
        with pytest.raises(DataSourceConnectionError, match="Host SFTP non configurato"):
            ds.connect()

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_paramiko_not_installed(self, mock_import):
        mock_import.return_value = None
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="paramiko"):
            ds.connect()

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_auth_failure(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        mock_ssh = MagicMock()
        paramiko.SSHClient.return_value = mock_ssh
        paramiko.AuthenticationException = type("AuthenticationException", (Exception,), {})
        mock_ssh.connect.side_effect = paramiko.AuthenticationException("bad")

        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Autenticazione"):
            ds.connect()

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_root_not_accessible(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        paramiko.SSHClient.return_value = mock_ssh
        mock_ssh.open_sftp.return_value = mock_sftp
        mock_sftp.chdir.side_effect = IOError("No such dir")
        paramiko.AuthenticationException = type("AE", (Exception,), {})
        paramiko.SSHException = type("SE", (Exception,), {})

        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Root path"):
            ds.connect()


# ========================== disconnect ====================================

class TestDisconnect:

    def test_closes_both(self):
        ds = _connected_ds()
        sftp = ds._sftp
        ssh = ds._ssh
        ds.disconnect()
        sftp.close.assert_called_once()
        ssh.close.assert_called_once()
        assert ds._sftp is None
        assert ds._ssh is None

    def test_close_error_ignored(self):
        ds = _connected_ds()
        ds._sftp.close.side_effect = Exception("err")
        ds._ssh.close.side_effect = Exception("err")
        ds.disconnect()  # no exception
        assert ds._sftp is None

    def test_already_disconnected(self):
        ds = _ds()
        ds.disconnect()  # no error


# ========================== test_connection ===============================

class TestTestConnection:

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_success(self, mock_import):
        paramiko = MagicMock()
        mock_import.return_value = paramiko
        mock_ssh = MagicMock()
        mock_sftp = MagicMock()
        paramiko.SSHClient.return_value = mock_ssh
        mock_ssh.open_sftp.return_value = mock_sftp

        # Simulate listdir_attr with regular files
        entry = MagicMock()
        entry.st_mode = stat.S_IFREG | 0o644
        mock_sftp.listdir_attr.return_value = [entry]

        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is True
        assert result["details"]["files_in_root"] == 1

    @patch("app.core.datasources.sftp._import_paramiko")
    def test_failure(self, mock_import):
        mock_import.return_value = None
        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is False


# ========================== list_files ====================================

class TestListFiles:

    def test_success(self):
        ds = _connected_ds()
        entry = MagicMock()
        entry.st_mode = stat.S_IFREG | 0o644
        entry.filename = "data.csv"
        entry.st_size = 100
        entry.st_mtime = 1700000000
        ds._sftp.listdir_attr.return_value = [entry]

        files = ds.list_files()
        assert len(files) == 1
        assert files[0].name == "data.csv"

    def test_filters_dirs(self):
        ds = _connected_ds()
        file_entry = MagicMock()
        file_entry.st_mode = stat.S_IFREG | 0o644
        file_entry.filename = "f.txt"
        file_entry.st_size = 10
        file_entry.st_mtime = 1700000000

        dir_entry = MagicMock()
        dir_entry.st_mode = stat.S_IFDIR | 0o755
        dir_entry.filename = "subdir"
        ds._sftp.listdir_attr.return_value = [file_entry, dir_entry]

        files = ds.list_files()
        assert len(files) == 1

    def test_pattern(self):
        ds = _connected_ds()
        e1 = MagicMock(st_mode=stat.S_IFREG | 0o644, filename="a.csv", st_size=1, st_mtime=1700000000)
        e2 = MagicMock(st_mode=stat.S_IFREG | 0o644, filename="b.txt", st_size=1, st_mtime=1700000000)
        ds._sftp.listdir_attr.return_value = [e1, e2]
        files = ds.list_files(pattern="*.csv")
        assert len(files) == 1

    def test_path_not_found(self):
        ds = _connected_ds()
        ds._sftp.listdir_attr.side_effect = IOError("No such dir")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.list_files(path="nope")


# ========================== file_info =====================================

class TestFileInfo:

    def test_success(self):
        ds = _connected_ds()
        attrs = MagicMock()
        attrs.st_size = 999
        attrs.st_mtime = 1700000000
        ds._sftp.stat.return_value = attrs
        fi = ds.file_info("sub/data.csv")
        assert fi.name == "data.csv"
        assert fi.size == 999

    def test_not_found(self):
        ds = _connected_ds()
        ds._sftp.stat.side_effect = IOError("No such file")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.file_info("nope.csv")


# ========================== read_file =====================================

class TestReadFile:

    def test_success(self):
        ds = _connected_ds()
        mock_file = MagicMock()
        mock_file.read.return_value = b"content"
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        ds._sftp.open.return_value = mock_file
        assert ds.read_file("f.txt") == b"content"

    def test_not_found(self):
        ds = _connected_ds()
        ds._sftp.open.side_effect = IOError("No such file")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.read_file("nope.txt")

    def test_permission_error(self):
        ds = _connected_ds()
        ds._sftp.open.side_effect = IOError("Permission denied")
        with pytest.raises(DataSourcePermissionError):
            ds.read_file("locked.txt")


# ========================== write_file ====================================

class TestWriteFile:

    def test_success(self):
        ds = _connected_ds()
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        ds._sftp.open.return_value = mock_file
        ds.write_file("new.txt", b"data")
        mock_file.write.assert_called_once_with(b"data")

    def test_creates_parent(self):
        ds = _connected_ds()
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        ds._sftp.open.return_value = mock_file
        ds.write_file("a/b/file.txt", b"x")
        assert ds._sftp.mkdir.called

    def test_permission_error(self):
        ds = _connected_ds()
        ds._sftp.open.side_effect = IOError("Permission denied")
        with pytest.raises(DataSourcePermissionError):
            ds.write_file("f.txt", b"x")


# ========================== delete_file ===================================

class TestDeleteFile:

    def test_success(self):
        ds = _connected_ds()
        ds.delete_file("old.txt")
        ds._sftp.remove.assert_called_once_with("old.txt")

    def test_not_found(self):
        ds = _connected_ds()
        ds._sftp.remove.side_effect = IOError("No such file")
        with pytest.raises(DataSourceFileNotFoundError):
            ds.delete_file("nope.txt")

    def test_permission_error(self):
        ds = _connected_ds()
        ds._sftp.remove.side_effect = IOError("Permission denied")
        with pytest.raises(DataSourcePermissionError):
            ds.delete_file("locked.txt")


# ========================== file_exists ===================================

class TestFileExists:

    def test_exists(self):
        ds = _connected_ds()
        ds._sftp.stat.return_value = MagicMock()
        assert ds.file_exists("data.csv") is True

    def test_not_exists(self):
        ds = _connected_ds()
        ds._sftp.stat.side_effect = IOError("No such file")
        assert ds.file_exists("nope.csv") is False


# ========================== _load_key =====================================

class TestLoadKey:

    def test_rsa_success(self):
        paramiko = MagicMock()
        paramiko.RSAKey.from_private_key.return_value = MagicMock()
        key = SFTPDataSource._load_key(paramiko, "PEM_DATA", None)
        assert key is not None

    def test_ed25519_fallback(self):
        paramiko = MagicMock()
        paramiko.SSHException = Exception
        paramiko.RSAKey.from_private_key.side_effect = paramiko.SSHException("bad")
        paramiko.Ed25519Key.from_private_key.return_value = MagicMock()
        key = SFTPDataSource._load_key(paramiko, "PEM_DATA", None)
        assert key is not None

    def test_ecdsa_fallback(self):
        paramiko = MagicMock()
        paramiko.SSHException = Exception
        paramiko.RSAKey.from_private_key.side_effect = paramiko.SSHException("bad")
        paramiko.Ed25519Key.from_private_key.side_effect = paramiko.SSHException("bad")
        paramiko.ECDSAKey.from_private_key.return_value = MagicMock()
        key = SFTPDataSource._load_key(paramiko, "PEM_DATA", None)
        assert key is not None

    def test_all_fail(self):
        paramiko = MagicMock()
        paramiko.SSHException = Exception
        for cls_name in ["RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey"]:
            getattr(paramiko, cls_name).from_private_key.side_effect = paramiko.SSHException("bad")
        with pytest.raises(DataSourceConnectionError, match="Impossibile caricare"):
            SFTPDataSource._load_key(paramiko, "PEM_DATA", None)

    def test_passphrase_encoded(self):
        paramiko = MagicMock()
        paramiko.RSAKey.from_private_key.return_value = MagicMock()
        SFTPDataSource._load_key(paramiko, "PEM", "secret")
        call_kwargs = paramiko.RSAKey.from_private_key.call_args
        assert call_kwargs[1]["password"] == b"secret"


# ========================== _mkdirs =======================================

class TestMkdirs:

    def test_creates_nested(self):
        ds = _connected_ds()
        ds._mkdirs("a/b/c")
        assert ds._sftp.mkdir.call_count == 3

    def test_ignores_existing(self):
        ds = _connected_ds()
        ds._sftp.mkdir.side_effect = IOError("exists")
        ds._mkdirs("a/b")  # no exception


# ========================== _check ========================================

class TestCheck:

    def test_not_connected(self):
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Non connesso"):
            ds._check()

    def test_connected(self):
        ds = _connected_ds()
        ds._check()  # no exception
