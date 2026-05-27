"""
Contract test C12 — ArcGIS token in Authorization header + retry 498/429.

Verifica:
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _make_response(status=200, json_body=None, headers=None):
    """Costruisce un response-like mock per i test."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=json_body or {})
    resp.headers = headers or {}
    return resp


def _make_conn(server="https://arcgis.example.com", user="alice", pwd="pwd"):
    """ArcGISConnectionInfo con auth_type=token."""
    from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
    return ArcGISConnectionInfo(
        server_url=server,
        server_type="arcgis_online",
        auth_type="token",
        username=user,
        password=pwd,
    )


# 1. Token nell'header Authorization, NON in params/data


def test_request_passes_token_in_authorization_header_not_params():
    """C12 anti-pattern fix: token DEVE viaggiare in Authorization
    header, non nei query params."""
    from app.core.features.arcgis_client import ArcGISClient

    mock_http = MagicMock()
    mock_http.get.return_value = _make_response(
        status=200, json_body={"services": []}
    )

    client = ArcGISClient(http_client=mock_http)
    # Bypass authenticate() side-effect con cache precompilata
    conn = _make_conn()
    cache_key = f"{conn.server_url}:{conn.username}"
    client._token_cache[cache_key] = {
        "token": "tok-AAA",
        "expires": 9_999_999_999,  # far future
    }

    client._request_with_token(
        "GET",
        f"{conn.server_url}/rest/services",
        conn,
        params={"f": "json"},
    )

    mock_http.get.assert_called_once()
    _, kwargs = mock_http.get.call_args
    # Token NON nei params
    assert "token" not in (kwargs.get("params") or {}), (
        "REGRESSIONE C12: token presente nei params "
        f"(vettore log reverse-proxy). params: {kwargs.get('params')}"
    )
    # Token SI nell'header
    headers = kwargs.get("headers") or {}
    assert headers.get("Authorization") == "Bearer tok-AAA", (
        f"Authorization: Bearer mancante. Headers: {headers}"
    )


def test_request_strips_token_from_params_if_legacy_caller_passes_it():
    """Difesa in profondita': se un caller dimenticato passa ancora
    token nei params, l'helper lo rimuove prima di emettere la request."""
    from app.core.features.arcgis_client import ArcGISClient

    mock_http = MagicMock()
    mock_http.get.return_value = _make_response(status=200, json_body={})

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    client._token_cache[f"{conn.server_url}:{conn.username}"] = {
        "token": "tok-BBB", "expires": 9_999_999_999,
    }

    client._request_with_token(
        "GET", f"{conn.server_url}/endpoint", conn,
        params={"f": "json", "token": "legacy-leak"},
    )

    _, kwargs = mock_http.get.call_args
    assert "token" not in (kwargs.get("params") or {})
    # Token autoritativo e' quello dall'header, non il "legacy-leak"
    assert kwargs["headers"]["Authorization"] == "Bearer tok-BBB"


def test_post_data_has_token_in_header_not_body():
    """POST case: token nel body data pre-C12. Current contract: header."""
    from app.core.features.arcgis_client import ArcGISClient

    mock_http = MagicMock()
    mock_http.post.return_value = _make_response(
        status=200, json_body={"success": True}
    )

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    client._token_cache[f"{conn.server_url}:{conn.username}"] = {
        "token": "tok-CCC", "expires": 9_999_999_999,
    }

    client._request_with_token(
        "POST", f"{conn.server_url}/layer/0/addFeatures", conn,
        data={"features": "[]", "f": "json"},
    )

    _, kwargs = mock_http.post.call_args
    assert "token" not in (kwargs.get("data") or {}), (
        "REGRESSIONE C12: token nel body POST."
    )
    assert kwargs["headers"]["Authorization"] == "Bearer tok-CCC"


# 2. Retry su 498/499 con refresh token


def test_request_retries_once_on_body_error_498():
    """Response body con error.code=498 → invalida cache, refresh
    token, retry UNA volta."""
    from app.core.features.arcgis_client import ArcGISClient

    mock_http = MagicMock()
    # Primo tentativo: 498 scaduto; secondo: success
    mock_http.get.side_effect = [
        _make_response(status=200, json_body={
            "error": {"code": 498, "message": "Invalid Token"}
        }),
        _make_response(status=200, json_body={"success": True}),
    ]

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    cache_key = f"{conn.server_url}:{conn.username}"
    client._token_cache[cache_key] = {
        "token": "tok-OLD", "expires": 9_999_999_999,
    }

    # Intercetta il 2o authenticate() per dare un token nuovo
    auth_calls = {"n": 0}

    def spy_auth(c):
        auth_calls["n"] += 1
        # Dopo invalidazione cache ritorna token nuovo
        if auth_calls["n"] == 1:
            return "tok-OLD"
        return "tok-NEW"

    client.authenticate = spy_auth

    response = client._request_with_token(
        "GET", f"{conn.server_url}/endpoint", conn,
    )
    assert response.json() == {"success": True}

    assert mock_http.get.call_count == 2, (
        "498 deve triggere retry, atteso 2 chiamate HTTP"
    )
    # Il secondo tentativo usa il token nuovo
    _, kwargs_2nd = mock_http.get.call_args_list[1]
    assert kwargs_2nd["headers"]["Authorization"] == "Bearer tok-NEW"
    # Cache invalidata tra le due chiamate (auth chiamato 2 volte)
    assert auth_calls["n"] == 2


def test_request_does_not_loop_on_repeated_498():
    """Se 498 persiste anche dopo refresh (token server-side revoked),
    l'helper ritorna la response senza loop infinito."""
    from app.core.features.arcgis_client import ArcGISClient

    mock_http = MagicMock()
    # Entrambi i tentativi ritornano 498
    mock_http.get.return_value = _make_response(
        status=200, json_body={"error": {"code": 498}}
    )

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    client._token_cache[f"{conn.server_url}:{conn.username}"] = {
        "token": "tok-X", "expires": 9_999_999_999,
    }
    client.authenticate = MagicMock(return_value="tok-X")

    response = client._request_with_token(
        "GET", f"{conn.server_url}/endpoint", conn,
    )

    # Al piu' 2 tentativi (originale + 1 retry post-refresh)
    assert mock_http.get.call_count == 2
    # Risposta 498 ritornata al caller (non solleva)
    assert response.json()["error"]["code"] == 498


# 3. 429 rate limit con backoff


def test_request_backoffs_and_retries_on_429(monkeypatch):
    """Response 429 → backoff (con Retry-After se presente) e retry
    fino a max_retries_429."""
    from app.core.features.arcgis_client import ArcGISClient

    sleeps = []
    monkeypatch.setattr("app.core.features.arcgis_auth.time.sleep",
                        lambda s: sleeps.append(s))

    mock_http = MagicMock()
    mock_http.get.side_effect = [
        _make_response(status=429, headers={"Retry-After": "0.1"}),
        _make_response(status=429, headers={"Retry-After": "0.2"}),
        _make_response(status=200, json_body={"ok": True}),
    ]

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    client._token_cache[f"{conn.server_url}:{conn.username}"] = {
        "token": "tok-Y", "expires": 9_999_999_999,
    }

    response = client._request_with_token(
        "GET", f"{conn.server_url}/endpoint", conn,
    )

    assert mock_http.get.call_count == 3
    assert response.status_code == 200
    # Primo sleep ~0.1 (Retry-After), secondo ~0.2
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.2)


def test_request_gives_up_on_429_after_max_retries(monkeypatch):
    """Dopo max_retries_429 tentativi falliti di 429, l'helper ritorna
    la response 429 al caller senza loop infinito."""
    from app.core.features.arcgis_client import ArcGISClient

    monkeypatch.setattr("app.core.features.arcgis_auth.time.sleep",
                        lambda s: None)

    mock_http = MagicMock()
    mock_http.get.return_value = _make_response(
        status=429, headers={"Retry-After": "0.01"}
    )

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    client._token_cache[f"{conn.server_url}:{conn.username}"] = {
        "token": "tok-Z", "expires": 9_999_999_999,
    }

    response = client._request_with_token(
        "GET", f"{conn.server_url}/endpoint", conn,
        max_retries_429=2,
    )

    # Originale + 2 retry = 3 chiamate (loop esce dopo la 3a 429)
    assert mock_http.get.call_count == 3
    assert response.status_code == 429


def test_request_uses_exponential_backoff_when_retry_after_is_malformed(monkeypatch):
    """Se Retry-After e' presente ma non parseabile, il client deve
    fare fallback al backoff interno invece di crashare o saltare il
    retry."""
    from app.core.features.arcgis_client import ArcGISClient

    sleeps = []
    monkeypatch.setattr("app.core.features.arcgis_auth.time.sleep",
                        lambda s: sleeps.append(s))

    mock_http = MagicMock()
    mock_http.get.side_effect = [
        _make_response(status=429, headers={"Retry-After": "not-a-number"}),
        _make_response(status=200, json_body={"ok": True}),
    ]

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    client._token_cache[f"{conn.server_url}:{conn.username}"] = {
        "token": "tok-M", "expires": 9_999_999_999,
    }

    response = client._request_with_token(
        "GET", f"{conn.server_url}/endpoint", conn,
    )

    assert response.status_code == 200
    assert mock_http.get.call_count == 2
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 30.0


# 4. Batch writer mid-batch refresh


def test_write_batch_refreshes_token_mid_batch_on_498(monkeypatch):
    """Scenario C12 critico: current contract OK, current contract ritorna 498 (token
    scaduto mid-batch), current contract retry con token nuovo ok, current contract ok.
    Pre-C12 current contract e 3 fallivano tutti."""
    from app.core.features.arcgis_client import ArcGISClient

    mock_http = MagicMock()
    # Pre-auth call (_authenticate_token genera token)
    mock_http.post.side_effect = [
        # current contract: success
        _make_response(status=200, json_body={
            "addResults": [{"success": True}] * 2,
        }),
        # current contract: 498 (token scaduto)
        _make_response(status=200, json_body={"error": {"code": 498}}),
        # current contract retry: success
        _make_response(status=200, json_body={
            "addResults": [{"success": True}] * 2,
        }),
        # current contract: success
        _make_response(status=200, json_body={
            "addResults": [{"success": True}] * 2,
        }),
    ]

    client = ArcGISClient(http_client=mock_http)
    conn = _make_conn()
    cache_key = f"{conn.server_url}:{conn.username}"
    client._token_cache[cache_key] = {
        "token": "tok-1", "expires": 9_999_999_999,
    }

    # authenticate() viene chiamato una volta per HTTP request + 1 retry
    # post-498. tok-1 scade al current contract, poi tok-2 subentra.
    _token_state = {"current": "tok-1"}

    def _simulated_auth(c):
        # Simula "cache invalidation after 498": se la cache interna del
        # client e' stata svuotata dal helper, ritorna il token nuovo.
        cache = client._token_cache.get(
            f"{c.server_url}:{c.username or c.client_id}"
        )
        if not cache:
            _token_state["current"] = "tok-2"
            client._token_cache[f"{c.server_url}:{c.username or c.client_id}"] = {
                "token": _token_state["current"],
                "expires": 9_999_999_999,
            }
        return _token_state["current"]

    client.authenticate = _simulated_auth

    features = [{"attributes": {"id": i}} for i in range(6)]
    result = client._write_batch(
        conn=conn,
        layer_url="https://arcgis.example.com/FeatureServer/0",
        features=features,
        operation="addFeatures",
        batch_size=2,
    )

    # 3 batch attesi + 1 retry del current contract = 4 HTTP calls
    assert mock_http.post.call_count == 4, (
        f"Attese 4 HTTP calls (3 batch + 1 refresh retry). "
        f"Effettive: {mock_http.post.call_count}"
    )
    # Tutti i batch sono andati a buon fine grazie al refresh
    assert result.get("added_count") == 6 or result.get("success") is True, (
        f"Risultato inatteso post-refresh: {result}"
    )

    # Tutte le chiamate devono usare Authorization Bearer e preservare
    # il Referer richiesto da alcuni token client=referer.
    auth_headers = []
    referers = []
    for _, kwargs in mock_http.post.call_args_list:
        headers = kwargs.get("headers") or {}
        data = kwargs.get("data") or {}
        auth_headers.append(headers.get("Authorization"))
        referers.append(headers.get("Referer"))
        assert "token" not in data, (
            f"REGRESSIONE C12: token nel body batch POST: {data}"
        )

    assert auth_headers == [
        "Bearer tok-1",
        "Bearer tok-1",
        "Bearer tok-2",
        "Bearer tok-2",
    ]
    assert all(r == conn.server_url for r in referers)
