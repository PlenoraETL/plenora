"""Contract test F5-B4 (2026-04-24) — SharePoint sanitize Office365 SDK leak.

Blinda: il path SharePoint non espone mai str(e) Office365-REST/msal/
requests raw nei messaggi utente (test_connection + DataSourceConnectionError).
Il dettaglio tecnico (site_url autenticato, tenant_id, client_id, Bearer
token, AADSTS code, HTTP response body) resta nei log server-side via
logger.warning con exc_info=True.

Regression history: prima del fix, 9 punti in sharepoint.py leakavano
str(e) user-facing:
  - connect auth catch f"Autenticazione SharePoint fallita: {e}"
  - test_connection catch Exception f"Errore inatteso: {e}"
  - 6 operazioni file: raise DataSourceConnectionError(f"Errore <op>: {e}")
  - _upload_chunked: idem

Coerente con F5-B2 (FTP/SFTP) + F5-B3 (MSSQL): mapping canonico italiano,
priorita' AADSTS -> HTTP status -> network/TLS -> fallback.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.datasources.interface import (
    BackendConfig,
    DataSourceConnectionError,
)
from app.core.datasources.sharepoint import (
    SharePointDataSource,
    _sanitize_sharepoint_error,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


# ─── Mapping helper: AADSTS / OAuth auth ────────────────────────────────────


def test_sanitize_maps_aadsts_to_auth_fail():
    """AADSTS token in str(e) → messaggio canonico auth; token NON leakato."""
    e = Exception(
        "AADSTS50126: Error validating credentials for user 'svc@SECRET_TENANT.onmicrosoft.com'"
    )
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Autenticazione SharePoint fallita: verifica tenant/client_id/secret"
    assert "AADSTS50126" not in msg
    assert "SECRET_TENANT" not in msg


def test_sanitize_maps_invalid_client_to_client_credentials_error():
    """OAuth2 invalid_client (senza AADSTS code) → client credentials canonical.

    Nota: la priorita' del mapping mette AADSTS prima di invalid_client.
    Se entrambi presenti, AADSTS vince (piu' specifico di Azure AD).
    Questo test copre il caso "invalid_client senza code AADSTS" (es.
    errori da client OAuth2 generico non-Microsoft)."""
    e = Exception("invalid_client response: unknown client_id=SECRET_GUID_CLIENT")
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Autenticazione SharePoint fallita: client credentials non valide"
    assert "SECRET_GUID_CLIENT" not in msg


# ─── Mapping helper: HTTP status codes ──────────────────────────────────────


def test_sanitize_maps_401_unauthorized_to_canonical():
    """401 / unauthorized → canonical; URL autenticato non leakato."""
    e = Exception(
        "401 Client Error: Unauthorized for url https://SECRET_TENANT.sharepoint.com/_api/web"
    )
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Accesso SharePoint non autorizzato"
    assert "SECRET_TENANT" not in msg
    assert "_api/web" not in msg


def test_sanitize_maps_404_to_not_found():
    """404 HTTP / not found → canonical, senza leak path."""
    e = Exception("404 Not Found for /Lists/SECRET_LIST_NAME/Items")
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Risorsa SharePoint non trovata"
    assert "SECRET_LIST_NAME" not in msg


def test_sanitize_maps_throttle_429_to_canonical():
    """429 / throttle / too many requests → canonical."""
    e = Exception("429 Too Many Requests; Retry-After: 30 tenant=SECRET_TENANT")
    msg = _sanitize_sharepoint_error(e)
    assert "throttling" in msg
    assert "SECRET_TENANT" not in msg


def test_sanitize_maps_5xx_server_error():
    """500/502/503/504 → canonical server error."""
    e = Exception(" 503 Service Unavailable (correlation-id=SECRET_TRACE_ID)")
    msg = _sanitize_sharepoint_error(e)
    assert "server SharePoint" in msg
    assert "SECRET_TRACE_ID" not in msg


# ─── Mapping helper: network / TLS ──────────────────────────────────────────


def test_sanitize_maps_tls_keywords_to_tls_error():
    """SSL / TLS / certificate / handshake → canonical TLS."""
    e = Exception(
        "SSL: CERTIFICATE_VERIFY_FAILED certificate CN=SECRET_INTERNAL_CN "
        "issued by SECRET_CA"
    )
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Errore protocollo TLS con SharePoint"
    assert "SECRET_INTERNAL_CN" not in msg
    assert "SECRET_CA" not in msg


def test_sanitize_maps_timeout_to_canonical():
    """TimeoutError o 'timeout' nel messaggio → canonical."""
    e = TimeoutError("connect timeout to SECRET_HOST:443")
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Timeout connessione SharePoint"
    assert "SECRET_HOST" not in msg


def test_sanitize_maps_generic_to_fallback():
    """Eccezione senza pattern noto → fallback canonico, nessun leak."""
    e = RuntimeError("qualcosa di strano con Bearer eyJSECRET_TOKEN_VALUE_HERE")
    msg = _sanitize_sharepoint_error(e)
    assert msg == "Errore connessione SharePoint"
    assert "SECRET_TOKEN_VALUE_HERE" not in msg
    assert "Bearer" not in msg


# ─── End-to-end: SharePointDataSource._test_connection / ops ───────────────


def _make_ds() -> SharePointDataSource:
    config = BackendConfig(
        id="ds-sp",
        type="sharepoint",
        root_path="Documenti",
        extra={
            "site_url": "https://tenant.example.test/sites/Test",
            "client_id": "fake-client-id",
            "client_secret": "fake-client-secret",
            "tenant_id": "fake-tenant-id",
        },
    )
    return SharePointDataSource(config)


def test_connect_sanitizes_office365_sdk_leak_in_auth_exception():
    """F5-B4: il catch generic in connect() sanitizza str(e) SDK prima
    di wrappare in DataSourceConnectionError. Il cause preserva il raw."""
    ds = _make_ds()

    with patch(
        "app.core.datasources.sharepoint._import_office365",
        return_value=(MagicMock(), MagicMock(side_effect=Exception(
            "AADSTS7000215: invalid_client for client_id=SECRET_GUID "
            "tenant=SECRET_TENANT Bearer eyJSECRET_TOKEN"
        ))),
    ):
        with pytest.raises(DataSourceConnectionError) as exc_info:
            ds.connect()

    msg = str(exc_info.value)
    # Il messaggio utente e' canonico
    assert "Autenticazione SharePoint" in msg or "client credentials" in msg
    # Anti-leak
    assert "AADSTS7000215" not in msg
    assert "SECRET_GUID" not in msg
    assert "SECRET_TENANT" not in msg
    assert "SECRET_TOKEN" not in msg
    assert "Bearer" not in msg


def test_test_connection_sanitizes_generic_exception_no_leak():
    """F5-B4: il catch Exception in test_connection (ex 'Errore inatteso: {e}')
    ora usa _sanitize_sharepoint_error — no leak di SDK raw."""
    ds = _make_ds()

    # Simula che l'import fallisca subito con un RuntimeError generico
    # non-DataSourceConnectionError → cade nel catch Exception
    with patch(
        "app.core.datasources.sharepoint._import_office365",
        side_effect=RuntimeError(
            "SDK crash inatteso with SECRET_SITE_URL=https://tenant.example"
        ),
    ):
        result = ds.test_connection()

    assert result["ok"] is False
    assert "SECRET_SITE_URL" not in result["message"]
    assert "SDK crash inatteso" not in result["message"]
    # Fallback canonico
    assert "SharePoint" in result["message"]


def test_sharepoint_log_preserves_technical_detail(monkeypatch):
    """F5-B4: il logger.warning server-side riceve type(e).__name__ + str(e)
    full per troubleshooting, nonostante il messaggio utente sia sanitizzato.

    Usa monkeypatch su logger.warning (caplog inconsistente tra run
    singolo/gruppo per interazioni con altri conftest — stesso problema
    gia' documentato in F5-B2)."""
    from app.core.datasources import sharepoint as sp_module

    captured_warnings = []
    original_warning = sp_module.logger.warning

    def _capture(msg, *args, **kwargs):
        captured_warnings.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(sp_module.logger, "warning", _capture)

    ds = _make_ds()

    class _AuthError(Exception):
        pass

    def _fake_import():
        # Ritorna classi che falliscono quando istanziate: simula Office365 auth fail
        cred_cls = MagicMock(side_effect=_AuthError(
            "AADSTS90001 TECHNICAL_DETAIL_FOR_LOGS_ONLY"
        ))
        ctx_cls = MagicMock()
        return cred_cls, ctx_cls

    with patch("app.core.datasources.sharepoint._import_office365", _fake_import):
        with pytest.raises(DataSourceConnectionError):
            ds.connect()

    all_log_text = " ".join(captured_warnings)
    assert "TECHNICAL_DETAIL_FOR_LOGS_ONLY" in all_log_text, (
        f"dettaglio SDK deve essere nei log (observability contract): "
        f"records = {captured_warnings}"
    )
    assert "_AuthError" in all_log_text or "AADSTS" in all_log_text
