"""
Unit tests for datasources/ftp.py — connect, disconnect, test_connection.
All FTP interactions are mocked via ftplib.FTP / ftplib.FTP_TLS.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import ftplib

pytestmark = pytest.mark.unit

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
)
from app.core.datasources.ftp import FTPDataSource


def _cfg(**extra):
    return BackendConfig(id="test", type="ftp", root_path="/data", extra=extra)


def _ds(**extra):
    defaults = {"host": "ftp.example.com", "port": 21, "username": "u", "password": "p"}
    defaults.update(extra)
    return FTPDataSource(_cfg(**defaults))


# ========================== connect =======================================

class TestConnect:

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_plain_ftp(self, MockFTP):
        mock_ftp = MagicMock()
        MockFTP.return_value = mock_ftp
        ds = _ds()
        ds.connect()
        MockFTP.assert_called_once()
        mock_ftp.connect.assert_called_once_with(host="ftp.example.com", port=21, timeout=15)
        mock_ftp.login.assert_called_once_with(user="u", passwd="p")
        mock_ftp.set_pasv.assert_called_once_with(True)
        mock_ftp.cwd.assert_called_once_with("/data")
        assert ds._ftp is mock_ftp

    @patch("app.core.datasources.ftp.ftplib.FTP_TLS")
    def test_tls_ftp(self, MockTLS):
        mock_ftp = MagicMock()
        MockTLS.return_value = mock_ftp
        ds = _ds(tls=True)
        ds.connect()
        MockTLS.assert_called_once()
        mock_ftp.prot_p.assert_called_once()

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_passive_false(self, MockFTP):
        mock_ftp = MagicMock()
        MockFTP.return_value = mock_ftp
        ds = _ds(passive=False)
        ds.connect()
        mock_ftp.set_pasv.assert_called_once_with(False)

    def test_no_host_raises(self):
        ds = FTPDataSource(_cfg(host="", username="u", password="p"))
        with pytest.raises(DataSourceConnectionError, match="Host FTP non configurato"):
            ds.connect()

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_root_not_accessible(self, MockFTP):
        mock_ftp = MagicMock()
        mock_ftp.cwd.side_effect = ftplib.error_perm("550 No such dir")
        MockFTP.return_value = mock_ftp
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Root path FTP non accessibile"):
            ds.connect()

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_connection_failure(self, MockFTP):
        mock_ftp = MagicMock()
        mock_ftp.connect.side_effect = OSError("timeout")
        MockFTP.return_value = mock_ftp
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Connessione FTP fallita"):
            ds.connect()

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_skip_cwd_if_root_empty(self, MockFTP):
        mock_ftp = MagicMock()
        MockFTP.return_value = mock_ftp
        cfg = BackendConfig(id="t", type="ftp", root_path="", extra={
            "host": "h", "username": "u", "password": "p",
        })
        ds = FTPDataSource(cfg)
        ds.connect()
        mock_ftp.cwd.assert_not_called()

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_skip_cwd_if_root_slash(self, MockFTP):
        mock_ftp = MagicMock()
        MockFTP.return_value = mock_ftp
        cfg = BackendConfig(id="t", type="ftp", root_path="/", extra={
            "host": "h", "username": "u", "password": "p",
        })
        ds = FTPDataSource(cfg)
        ds.connect()
        mock_ftp.cwd.assert_not_called()


# ========================== disconnect ====================================

class TestDisconnect:

    def test_quit_ok(self):
        ds = _ds()
        ds._ftp = MagicMock()
        ds.disconnect()
        ds._ftp_saved = None  # already None after disconnect
        # The MagicMock's quit was called before _ftp was set to None
        assert ds._ftp is None

    def test_quit_fails_close_fallback(self):
        ds = _ds()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = Exception("quit error")
        ds._ftp = mock_ftp
        ds.disconnect()
        mock_ftp.close.assert_called_once()
        assert ds._ftp is None

    def test_quit_and_close_both_fail(self):
        ds = _ds()
        mock_ftp = MagicMock()
        mock_ftp.quit.side_effect = Exception("quit error")
        mock_ftp.close.side_effect = Exception("close error")
        ds._ftp = mock_ftp
        ds.disconnect()  # should not raise
        assert ds._ftp is None

    def test_disconnect_when_not_connected(self):
        ds = _ds()
        ds._ftp = None
        ds.disconnect()  # no error
        assert ds._ftp is None


# ========================== test_connection ===============================

class TestTestConnection:

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_success(self, MockFTP):
        mock_ftp = MagicMock()
        mock_ftp.nlst.return_value = ["a.txt", "b.csv"]
        MockFTP.return_value = mock_ftp
        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is True
        assert result["details"]["root_entries"] == 2
        assert ds._ftp is None  # disconnected after

    @patch("app.core.datasources.ftp.ftplib.FTP")
    def test_failure(self, MockFTP):
        mock_ftp = MagicMock()
        mock_ftp.connect.side_effect = OSError("refused")
        MockFTP.return_value = mock_ftp
        ds = _ds()
        result = ds.test_connection()
        assert result["ok"] is False
        assert ds._ftp is None


# ========================== _check ========================================

class TestCheck:

    def test_not_connected_raises(self):
        ds = _ds()
        with pytest.raises(DataSourceConnectionError, match="Non connesso"):
            ds._check()

    def test_connected_ok(self):
        ds = _ds()
        ds._ftp = MagicMock()
        ds._check()  # no exception
