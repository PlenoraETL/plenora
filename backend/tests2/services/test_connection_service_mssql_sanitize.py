"""
Contract test F5-B3 (2026-04-24) — `_test_mssql` sanitize str(e) SDK leak.

Blinda: il path MS SQL di `ConnectionService._test_mssql` non espone mai
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Unit tests use a fake pyodbc module when the optional driver is absent.
if "pyodbc" not in sys.modules:
    fake_pyodbc = types.ModuleType("pyodbc")
    fake_pyodbc.drivers = lambda: []
    fake_pyodbc.connect = lambda *args, **kwargs: None
    sys.modules["pyodbc"] = fake_pyodbc

from app.core.connections.service import (  # noqa: E402
    ConnectionService,
    _sanitize_mssql_error,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


class _PyodbcLikeError(Exception):
    """Fake pyodbc.Error. str(e) rispecchia il formato reale:
    '[SQLSTATE] [vendor][driver][provider]message'."""


def test_sanitize_mssql_error_maps_sqlstate_28000_to_auth_fail():
    """SQLSTATE [28000] = Login failed → messaggio auth canonico."""
    e = _PyodbcLikeError(
        "[28000] [Microsoft][ODBC Driver 17 for SQL Server]"
        "Login failed for user 'admin@SECRET_HOST'"
    )
    msg = _sanitize_mssql_error(e)
    assert msg == "Autenticazione MS SQL fallita: credenziali non valide"
    # Anti-leak sul mapping stesso
    assert "SECRET_HOST" not in msg
    assert "28000" not in msg
    assert "admin" not in msg


def test_sanitize_mssql_error_maps_sqlstate_08001_to_connection_refused():
    """SQLSTATE [08001] o 'Cannot open server' → host irraggiungibile."""
    e = _PyodbcLikeError(
        "[08001] [Microsoft][ODBC Driver 18]"
        "Cannot open server 'internal-db.corp.example:1433'"
    )
    msg = _sanitize_mssql_error(e)
    assert msg == "Connessione MS SQL rifiutata o host irraggiungibile"
    assert "internal-db.corp.example" not in msg
    assert "1433" not in msg


def test_sanitize_mssql_error_maps_sqlstate_08004_to_timeout():
    """SQLSTATE [08004] o 'Login timeout expired' → timeout."""
    e = _PyodbcLikeError("[08004] Login timeout expired after 5s with SECRET_HOST")
    msg = _sanitize_mssql_error(e)
    assert msg == "Timeout connessione MS SQL"
    assert "SECRET_HOST" not in msg


def test_sanitize_mssql_error_maps_tls_keywords_to_tls_error():
    """Keyword TLS/SSL/handshake → messaggio protocollo TLS dedicato."""
    e = _PyodbcLikeError(
        "SSL Provider: The certificate chain was issued by an untrusted authority "
        "(certificate CN=SECRET_INTERNAL_CN)"
    )
    msg = _sanitize_mssql_error(e)
    assert msg == "Errore certificato TLS MS SQL: catena non attendibile o nome host non valido"
    assert "SECRET_INTERNAL_CN" not in msg


def test_sanitize_mssql_error_generic_exception_returns_canonical_fallback():
    """Eccezione senza pattern noto → messaggio canonico fallback."""
    e = RuntimeError("qualche errore davvero inatteso con TOKEN_XYZ_LEAK")
    msg = _sanitize_mssql_error(e)
    # Un match su 'token' non e' previsto, cade in fallback
    assert msg == "Errore connessione MS SQL"
    assert "TOKEN_XYZ_LEAK" not in msg


def _make_mssql_conn():
    """Connection object minimal per chiamare _test_mssql."""
    c = MagicMock()
    c.type = "mssql"
    c.host = "mssql.example.test"
    c.port = 1433
    c.database = "testdb"
    c.username = "user"
    c.password = "pw"
    return c


def test_test_mssql_does_not_leak_raw_sqlstate_in_message_or_details():
    """Anti-leak primario: pyodbc raw NON compare ne' in result.message
    ne' in result.details['errors']. Il dettaglio (SECRET_HOSTNAME, UID,
    SQLSTATE) resta solo nel log server-side."""
    service = ConnectionService()

    def _fake_pyodbc_connect(cs, timeout):
        raise _PyodbcLikeError(
            "[28000] [Microsoft][ODBC Driver 17 for SQL Server]"
            "Login failed for user 'SECRET_UID@SECRET_HOSTNAME:1433' "
            "driver=v17.10"
        )

    # Mock pyodbc.drivers per fingere driver installati
    fake_drivers = lambda: ["ODBC Driver 17 for SQL Server"]

    with patch("pyodbc.drivers", fake_drivers), \
         patch("pyodbc.connect", side_effect=_fake_pyodbc_connect):
        result = service._test_mssql(_make_mssql_conn())

    assert result.success is False
    # Anti-leak: payload utente
    assert "SECRET_HOSTNAME" not in result.message
    assert "SECRET_UID" not in result.message
    assert "28000" not in result.message
    assert "v17.10" not in result.message
    # details["errors"] e' sanitizzato anch'esso
    details = result.details or {}
    errs = details.get("errors", [])
    all_errs_text = " ".join(errs) if errs else ""
    assert "SECRET_HOSTNAME" not in all_errs_text, (
        f"leak in details['errors']: {errs!r}"
    )
    assert "SECRET_UID" not in all_errs_text
    assert "28000" not in all_errs_text
    # Il messaggio canonico DEVE essere presente
    assert "Autenticazione MS SQL" in all_errs_text or "credenziali" in all_errs_text


def test_test_mssql_aggregated_message_cites_sanitized_first_error():
    """Flusso "tutti i driver falliti": il message aggregato cita
    `errors[0]` come "primo errore", e quel valore DEVE essere sanitized.
    Regressione: prima del fix, errors[0] conteneva pyodbc raw → il
    fan-out propagava il leak nel message utente."""
    service = ConnectionService()

    def _fake_connect(cs, timeout):
        raise _PyodbcLikeError(
            "[08001] Cannot open server SECRET_INTERNAL_HOST "
            "requested by the login. The login failed for UID=SECRET_UID."
        )

    fake_drivers = lambda: [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
    ]

    with patch("pyodbc.drivers", fake_drivers), \
         patch("pyodbc.connect", side_effect=_fake_connect):
        result = service._test_mssql(_make_mssql_conn())

    assert result.success is False
    # Il message cita "Primo errore: ..." → DEVE essere sanitized
    assert "SECRET_INTERNAL_HOST" not in result.message, (
        f"leak nel message aggregato (primo errore propagato): {result.message!r}"
    )
    assert "SECRET_UID" not in result.message
    assert "08001" not in result.message
    # Il messaggio canonico del primo errore deve comparire nel message
    assert "Connessione MS SQL" in result.message or "irraggiungibile" in result.message


def test_test_mssql_log_preserves_technical_detail(monkeypatch):
    """Mentre il messaggio utente e' sanitizzato, logger.warning server-side
    riceve il dettaglio raw per troubleshooting (type(e).__name__ + str(e))."""
    from app.core.connections import service as cs_module

    captured_warnings = []
    original_warning = cs_module.logger.warning

    def _capture(msg, *args, **kwargs):
        captured_warnings.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(cs_module.logger, "warning", _capture)

    service = ConnectionService()

    def _fake_connect(cs, timeout):
        raise _PyodbcLikeError(
            "[28000] Login failed TECHNICAL_DETAIL_FOR_LOGS_ONLY"
        )

    fake_drivers = lambda: ["ODBC Driver 17 for SQL Server"]

    with patch("pyodbc.drivers", fake_drivers), \
         patch("pyodbc.connect", side_effect=_fake_connect):
        result = service._test_mssql(_make_mssql_conn())

    assert result.success is False
    # Il dettaglio tecnico DEVE essere nei log
    all_log_text = " ".join(captured_warnings)
    assert "TECHNICAL_DETAIL_FOR_LOGS_ONLY" in all_log_text, (
        f"dettaglio SDK deve essere nei log (observability contract): "
        f"records = {captured_warnings}"
    )
    assert "_PyodbcLikeError" in all_log_text or "MS SQL driver" in all_log_text
