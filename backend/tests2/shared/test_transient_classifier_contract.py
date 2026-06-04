"""Contract: classificatore errori transitori `app.shared.resilience.is_transient`.

Whitelist conservativa per il retry (#3): True solo per cio' che e' riconosciuto
transitorio (rete/PG/HTTP-5xx), False per tutto il resto (errori applicativi,
auth, vincoli, file mancanti) — cosi' il retry non maschera bug ne' ritenta
operazioni non ripetibili.
"""

from __future__ import annotations

import errno
import socket

import pytest

from app.shared.resilience import (
    TRANSIENT_HTTP_STATUSES,
    is_transient,
    is_transient_http_status,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _FakeSqlstateError(Exception):
    """Simula un'eccezione psycopg-like con attributo `.sqlstate`."""

    def __init__(self, sqlstate: str):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _FakeHttpError(Exception):
    """Simula requests.HTTPError-like con `.response.status_code`."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = type("_R", (), {"status_code": status_code})()


# --- transitori (devono essere ritentati) ---------------------------------

@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("slow"),
        ConnectionError("dropped"),
        ConnectionResetError("reset"),
        ConnectionRefusedError("refused"),
        socket.gaierror("dns"),
        OSError(errno.ECONNRESET, "reset"),
        OSError(errno.ETIMEDOUT, "timeout"),
        _FakeSqlstateError("40001"),   # serialization_failure
        _FakeSqlstateError("40P01"),   # deadlock_detected
        _FakeSqlstateError("08006"),   # connection_failure (classe 08)
        _FakeSqlstateError("53300"),   # too_many_connections (classe 53)
        _FakeHttpError(429),
        _FakeHttpError(503),
        _FakeHttpError(500),
    ],
)
def test_transient_errors_are_retryable(exc):
    assert is_transient(exc) is True


# --- NON transitori (non vanno ritentati) ---------------------------------

@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad input"),
        KeyError("missing"),
        TypeError("oops"),
        PermissionError("denied"),
        FileNotFoundError("no file"),
        OSError(errno.ENOENT, "no such file"),   # OSError non di rete
        _FakeSqlstateError("23505"),             # unique_violation
        _FakeSqlstateError("42P01"),             # undefined_table
        _FakeHttpError(400),
        _FakeHttpError(404),
        _FakeHttpError(401),
        Exception("generic, sconosciuto"),
    ],
)
def test_non_transient_errors_are_not_retryable(exc):
    assert is_transient(exc) is False


def test_http_status_helper():
    for s in TRANSIENT_HTTP_STATUSES:
        assert is_transient_http_status(s) is True
    for s in (200, 201, 400, 401, 403, 404, 422, None, "boom"):
        assert is_transient_http_status(s) is False


def test_optional_sdk_types_are_recognized_when_present():
    """Se psycopg/requests sono installati (lo sono nel container), le loro
    eccezioni di connessione/timeout sono classificate transitorie."""
    try:
        import psycopg
    except Exception:
        psycopg = None
    if psycopg is not None:
        assert is_transient(psycopg.OperationalError("conn lost")) is True

    try:
        import requests
    except Exception:
        requests = None
    if requests is not None:
        assert is_transient(requests.exceptions.ConnectionError("refused")) is True
        assert is_transient(requests.exceptions.Timeout("slow")) is True
        # requests.HTTPError applicativo (404) NON e' transitorio
        http_err = requests.exceptions.HTTPError("not found")
        http_err.response = type("_R", (), {"status_code": 404})()
        assert is_transient(http_err) is False
