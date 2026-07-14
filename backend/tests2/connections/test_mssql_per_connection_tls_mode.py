from __future__ import annotations

from types import SimpleNamespace
import sys
import types

import pytest
from fastapi import HTTPException

from app.core.connections.models import ConnectionCreate, ConnectionUpdate
from app.core.connections.router import _assert_mssql_tls_change_allowed
from app.core.connections.service import ConnectionService
from app.core.query_execution.query_engines import DefaultEngineFactory
from app.shared.connections.mssql_tls import sqlalchemy_tls_query
from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


def _conn(mode: str = "verify") -> SimpleNamespace:
    return SimpleNamespace(
        type="mssql",
        host="sql.internal",
        port=1433,
        database="archibus",
        username="reader",
        password="secret",
        mssql_tls_mode=mode,
    )


def _user(role: str) -> CurrentUser:
    return CurrentUser(
        user_id="u-1", username="tester", group_id="root", role=role,
    )


def test_default_mode_verifies_certificate_and_requires_encryption():
    conn = _conn()
    assert sqlalchemy_tls_query(conn) == "Encrypt=Mandatory&TrustServerCertificate=no"
    url = DefaultEngineFactory()._costruisci_connection_string(conn)
    assert "Encrypt=Mandatory" in url
    assert "TrustServerCertificate=no" in url


def test_explicit_exception_keeps_encryption_mandatory():
    conn = _conn("trust_server_certificate")
    assert sqlalchemy_tls_query(conn) == "Encrypt=Mandatory&TrustServerCertificate=yes"
    url = DefaultEngineFactory()._costruisci_connection_string(conn)
    assert "Encrypt=Mandatory" in url
    assert "TrustServerCertificate=yes" in url
    assert "Encrypt=Optional" not in url
    assert "Encrypt=No" not in url


def test_tls_mode_changes_engine_cache_key():
    factory = DefaultEngineFactory()
    assert factory._connection_key(_conn()) != factory._connection_key(
        _conn("trust_server_certificate")
    )


def test_connection_test_uses_explicit_trust_without_disabling_encryption(
    monkeypatch,
):
    captured: list[str] = []

    class _Cursor:
        def execute(self, _sql):
            return None

        def fetchone(self):
            return ("Microsoft SQL Server",)

        def close(self):
            return None

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self):
            return None

    fake_pyodbc = types.ModuleType("pyodbc")
    fake_pyodbc.drivers = lambda: ["ODBC Driver 18 for SQL Server"]

    def _connect(connection_string, timeout):
        captured.append(connection_string)
        assert timeout == 5
        return _Connection()

    fake_pyodbc.connect = _connect
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)

    result = ConnectionService()._test_mssql(
        _conn("trust_server_certificate")
    )

    assert result.success is True
    assert len(captured) == 1
    assert "Encrypt=Mandatory" in captured[0]
    assert "TrustServerCertificate=yes" in captured[0]
    assert "Encrypt=Optional" not in captured[0]


def test_admin_cannot_enable_certificate_exception():
    data = ConnectionCreate(
        name="Archibus",
        type="mssql",
        host="sql.internal",
        port=1433,
        database="archibus",
        username="reader",
        password="secret",
        mssql_tls_mode="trust_server_certificate",
    )
    with pytest.raises(HTTPException) as exc:
        _assert_mssql_tls_change_allowed(data, _user("admin"))
    assert exc.value.status_code == 403


def test_superadmin_can_enable_certificate_exception():
    data = ConnectionUpdate(mssql_tls_mode="trust_server_certificate")
    _assert_mssql_tls_change_allowed(data, _user("superadmin"), _conn())


def test_invalid_tls_mode_is_rejected_by_api_model():
    with pytest.raises(ValueError):
        ConnectionUpdate(mssql_tls_mode="disable_encryption")
