"""Cluster 3 contract test: secret leak in logger su connections + oauth.

Bug originali (audit P0 cluster):
- connections P0 #1: `connections/service.py:391-394` — pymssql fallback
  logga `{e}` raw. FreeTDS su auth failure include username/password nel
  messaggio.
- connections P0 #2: `connections/service.py:409-413` — safety-net catch-all
  logga `{e}` raw. Stessa superficie.
- connections P0 #3: `shared/connections/oauth.py:633` — `get_arcgis_token`
  logga `%s, e` raw. Diverge dalle gemelle `_fetch_oauth2_password_token`
  e `_fetch_oauth2_client_credentials_token` gia' fixate.

Post-fix: tutti i 3 path ora loggano solo `type(e).__name__` + valore
sanitizzato; `exc_info=True` mantiene la traceback per debug
operativo ma NON inquina la stringa principale del log.

Pattern Go-friendly: spy sul logger via monkeypatch, iniezione di
eccezioni con credenziali sintetiche, assert che la credenziale NON
compare nei log catturati. Mappa a Go come `bytes.Buffer` come writer
del logger + `strings.Contains(buf.String(), fakeCred) == false`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_FAKE_PASSWORD = "P@ssw0rd-Fake-SECRET-AUDIT-123!"
_FAKE_USERNAME = "user-fake-leak-audit"


def _capture_logs(monkeypatch, logger_module_path):
    """Aggancia spy a logger.warning / logger.error / logger.info / logger.debug
    sul logger del modulo target. Ritorna lista di tutte le entry catturate."""
    import importlib

    captured = []
    mod = importlib.import_module(logger_module_path)
    logger = mod.logger

    def _spy(level):
        def fn(msg, *args, **kwargs):
            try:
                rendered = msg % args if args else msg
            except (TypeError, ValueError):
                rendered = f"{msg} {args}"
            captured.append((level, rendered))
        return fn

    monkeypatch.setattr(logger, "warning", _spy("warning"))
    monkeypatch.setattr(logger, "error", _spy("error"))
    monkeypatch.setattr(logger, "info", _spy("info"))
    monkeypatch.setattr(logger, "debug", _spy("debug"))
    return captured


# Path 1: connections/service.py pymssql fallback

def test_pymssql_fallback_does_not_leak_credentials_in_log(monkeypatch):
    """Pre-fix: f"... {e}" propagava raw pymssql.OperationalError che include
    username/password in auth failure. Post-fix: solo type(e).__name__ +
    sanitized."""
    from app.core.connections.service import ConnectionService
    from types import SimpleNamespace

    captured = _capture_logs(monkeypatch, "app.core.connections.service")

    # Skip pyodbc path: assicuriamoci che pyodbc non sia disponibile o
    # ritorni un set vuoto cosi' il flow finisce nel pymssql fallback.
    import sys

    # Stub pymssql in sys.modules con connect() che solleva con la
    # credenziale fake nel messaggio (simula il comportamento reale).
    class _FakePymssqlError(Exception):
        pass

    fake_pymssql = SimpleNamespace(
        connect=lambda **kw: (_ for _ in ()).throw(
            _FakePymssqlError(
                f"Login failed for user '{_FAKE_USERNAME}', "
                f"password='{_FAKE_PASSWORD}', host=10.0.0.1"
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "pymssql", fake_pymssql)

    # Forza il pyodbc path a non-trovato (no driver installato)
    fake_pyodbc = SimpleNamespace(drivers=lambda: [])
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)

    svc = ConnectionService.__new__(ConnectionService)
    conn = SimpleNamespace(
        type="mssql", host="10.0.0.1", port=1433,
        database="db", username=_FAKE_USERNAME, password=_FAKE_PASSWORD,
    )
    result = svc._test_mssql(conn)

    # La connessione fallisce (mock di pymssql.connect solleva)
    assert result.success is False

    # ANTI-REGRESSION: la password NON deve apparire in alcun log entry
    all_log = "\n".join(rendered for _level, rendered in captured)
    assert _FAKE_PASSWORD not in all_log, (
        f"Password leak in log! Pre-fix: f-string {{e}} propagava raw. "
        f"Log catturato (estratto): {all_log[:500]!r}"
    )


# Path 2: connections/service.py safety-net catch-all

def test_safety_net_path_uses_sanitized_logger_format():
    """Il safety-net catch-all in `_test_mssql` (try riga 330 → except 409)
    e' difficile da triggerare via unit test perche' tutti gli errori comuni
    sono catturati dai try annidati per-driver. Verifichiamo via source
    inspection che il pattern e' corretto (no f"{e}" raw, sanitized in uso).
    Coverage funzionale del path raw si fa via property-based test su input
    esotici in un layer integration successivo se necessario."""
    import inspect
    from app.core.connections import service as conn_service

    src = inspect.getsource(conn_service.ConnectionService._test_mssql)
    # Localizza il safety-net catch-all
    safety_idx = src.find("safety-net:")
    assert safety_idx >= 0, "Safety-net block non trovato"
    safety_block = src[safety_idx:safety_idx + 400]

    # Anti-regression: nessun pattern raw f"{e}" o "{e}" direttamente nel
    # logger del safety-net. Deve usare type(e).__name__ + sanitized.
    assert "type(e).__name__" in safety_block, (
        "Safety-net catch-all deve loggare type(e).__name__ (NON str(e) raw)"
    )
    assert ("_sanitize_mssql_error" in safety_block
            or "sanitized" in safety_block), (
        "Safety-net catch-all deve usare _sanitize_mssql_error (direttamente "
        "o via variabile sanitized assegnata appena sopra)"
    )


# Path 3: oauth.py get_arcgis_token

def test_arcgis_token_fetch_does_not_leak_in_log(monkeypatch):
    """Pre-fix: logger.error(`%s, e`) propagava lo str(e) raw. Post-fix:
    solo type(e).__name__ + exc_info."""
    import requests
    from app.shared.connections import oauth

    captured = _capture_logs(monkeypatch, "app.shared.connections.oauth")

    # Forziamo requests.post a sollevare con la credenziale nel messaggio
    class _FakeHTTPError(Exception):
        pass

    def _raise_with_secret(*args, **kwargs):
        raise _FakeHTTPError(
            f"HTTP 401 from token_url=https://arcgis/sharing/rest/oauth2/token"
            f"?client_id=foo&client_secret={_FAKE_PASSWORD}"
        )

    monkeypatch.setattr(requests, "post", _raise_with_secret)
    monkeypatch.setattr(oauth, "resolve_tls_verify", lambda *a, **kw: True)

    auth = {
        "token_url": "https://arcgis/sharing/rest/oauth2/token",
        "username": _FAKE_USERNAME,
        "password": _FAKE_PASSWORD,
        "client": "requestip",
    }
    # get_arcgis_token chiama _fetch_record internamente; in caso di
    # eccezione cattura e logga.
    result = oauth.get_arcgis_token(auth)

    # _fetch_record ritorna None su eccezione, ma get_arcgis_token usa
    # un cache layer; il risultato finale puo' essere None o un valore
    # cache-d. Verifichiamo SOLO il contratto del log.
    all_log = "\n".join(rendered for _level, rendered in captured)
    assert _FAKE_PASSWORD not in all_log, (
        f"client_secret leak in arcgis token log! Pre-fix: %s, e propagava "
        f"l'URL con i query params. Log: {all_log[:500]!r}"
    )


# Path 4: architectural assertion

def test_mssql_and_arcgis_log_paths_use_type_name_pattern():
    """Architectural lint: i 3 path target NON devono avere `f"... {e}"` o
    `"%s", e` pattern raw. Anti-regression contro futuri commit che
    re-introducano il leak."""
    import inspect
    from app.core.connections import service as conn_service
    from app.shared.connections import oauth

    # `_sanitize_mssql_error` deve essere usato (sia inline sia via variabile
    # `sanitized` assegnata appena prima del log) nei 2 path mssql.
    src_conn = inspect.getsource(conn_service)

    # Pattern anti-regression: NESSUN ramo `f"... {e}"` raw nei log
    # mssql, dove `e` e' una variabile di eccezione catturata. Sostituibili
    # solo con: type(e).__name__, sanitized=_sanitize_mssql_error(e), o
    # %s + tipo+sanitized. Stripping commenti per evitare false positive
    # su righe che documentano il fix (es. "Pre-fix: f\"{e}\" raw").
    src_no_comments = "\n".join(
        line for line in src_conn.splitlines() if not line.lstrip().startswith("#")
    )
    assert "f\"{e}\"" not in src_no_comments, (
        "Detected raw f\"{e}\" pattern in connections/service.py — possibile "
        "leak credenziali. Usa type(e).__name__ + sanitized."
    )
    pymssql_block_idx = src_conn.find("pymssql fallback")
    assert pymssql_block_idx >= 0, "Codice pymssql fallback non trovato"
    pymssql_block = src_conn[pymssql_block_idx:pymssql_block_idx + 600]
    assert (
        "_sanitize_mssql_error" in pymssql_block
        or ("sanitized" in pymssql_block and "type(e).__name__" in pymssql_block)
    ), (
        "Il path pymssql fallback deve usare _sanitize_mssql_error per il log "
        "(direttamente o tramite variabile `sanitized`)"
    )

    safety_idx = src_conn.find("_test_mssql safety-net")
    assert safety_idx >= 0
    safety_block = src_conn[safety_idx:safety_idx + 400]
    assert (
        "_sanitize_mssql_error" in safety_block
        or ("sanitized" in safety_block and "type(e).__name__" in safety_block)
    ), (
        "Il safety-net catch-all deve usare _sanitize_mssql_error per il log"
    )

    # get_arcgis_token: deve avere `type(e).__name__` nell'except
    src_oauth = inspect.getsource(oauth.get_arcgis_token)
    assert "type(e).__name__" in src_oauth, (
        "get_arcgis_token deve loggare type(e).__name__, non lo str(e) raw"
    )
