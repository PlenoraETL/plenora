"""
Contract test BE-IMDF-002 — `ArcGISClient.count_features_where`.

Verifica il boundary pubblico consolidato per il count features con
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures


def _make_response(
    *,
    status_code: int = 200,
    content_type: str = "application/json",
    body: object = None,
    raw_text: str | None = None,
):
    """Costruisce una response stub con `status_code`, `headers`,
    `text`, `json()`. `body` viene serializzato a JSON se `raw_text`
    e' None; altrimenti `text` = `raw_text` letterale e `json()` lo
    parse-a (o solleva ValueError)."""
    resp = MagicMock(name="Response")
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    if raw_text is not None:
        resp.text = raw_text
        try:
            parsed = json.loads(raw_text)
            resp.json = MagicMock(return_value=parsed)
        except Exception:
            resp.json = MagicMock(side_effect=ValueError("malformed JSON"))
    else:
        resp.text = json.dumps(body) if body is not None else ""
        resp.json = MagicMock(return_value=body)
    return resp


@pytest.fixture
def client_with_fake_http():
    """ArcGISClient con `_http.post` mockato e `authenticate` stub."""
    client = ArcGISClient.__new__(ArcGISClient)
    client._http = MagicMock(name="HttpClient")
    client.authenticate = MagicMock(return_value="tok-abc")
    return client


@pytest.fixture
def conn():
    return SimpleNamespace(
        server_url="https://arc.test",
        username="u",
    )


# Happy path


def test_count_features_where_happy_path(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 42})

    n = client_with_fake_http.count_features_where(
        conn, "https://arc.test/layer/0", "SITE_ID IN ('a','b')"
    )

    assert n == 42
    assert isinstance(n, int)


def test_count_features_where_returns_int_even_when_count_is_string(
    client_with_fake_http, conn,
):
    """ArcGIS a volte ritorna `count` come stringa numerica; il metodo
    fa `int(...)` per garantire tipo."""
    client_with_fake_http._http.post.return_value = _make_response(body={"count": "7"})
    assert client_with_fake_http.count_features_where(
        conn, "https://arc.test/layer/0", "1=1"
    ) == 7


def test_count_features_where_zero_when_count_missing(client_with_fake_http, conn):
    """Payload senza `count` → 0 (default `payload.get("count", 0)`)."""
    client_with_fake_http._http.post.return_value = _make_response(body={"x": 1})
    assert client_with_fake_http.count_features_where(
        conn, "https://arc.test/layer/0", "1=1"
    ) == 0


# Request shape: URL, body, headers, timeout


def test_count_features_where_post_url_is_layer_query(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(
        conn, "https://arc.test/svc/FeatureServer/3", "1=1"
    )

    call = client_with_fake_http._http.post.call_args
    assert call.args[0] == "https://arc.test/svc/FeatureServer/3/query"


def test_count_features_where_body_includes_required_params(
    client_with_fake_http, conn,
):
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(
        conn, "https://arc.test/layer/0", "SITE_ID = 'X'"
    )

    call = client_with_fake_http._http.post.call_args
    body = call.kwargs["data"]
    assert body["where"] == "SITE_ID = 'X'"
    assert body["returnCountOnly"] == "true"
    assert body["f"] == "json"


def test_count_features_where_includes_token_when_authenticated(
    client_with_fake_http, conn,
):
    client_with_fake_http.authenticate.return_value = "tok-XYZ"
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(conn, "https://arc.test/layer/0", "1=1")

    body = client_with_fake_http._http.post.call_args.kwargs["data"]
    assert body["token"] == "tok-XYZ"


def test_count_features_where_omits_token_when_not_authenticated(
    client_with_fake_http, conn,
):
    """Se `authenticate(conn)` ritorna None / "" il token NON va nel body."""
    client_with_fake_http.authenticate.return_value = None
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(conn, "https://arc.test/layer/0", "1=1")

    body = client_with_fake_http._http.post.call_args.kwargs["data"]
    assert "token" not in body


def test_count_features_where_sends_referer_header(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(conn, "https://arc.test/layer/0", "1=1")

    headers = client_with_fake_http._http.post.call_args.kwargs["headers"]
    assert headers == {"Referer": "https://arc.test"}


def test_count_features_where_default_timeout_60s(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(conn, "https://arc.test/layer/0", "1=1")

    timeout = client_with_fake_http._http.post.call_args.kwargs["timeout"]
    assert timeout == 60.0


def test_count_features_where_custom_timeout(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(body={"count": 0})

    client_with_fake_http.count_features_where(
        conn, "https://arc.test/layer/0", "1=1", timeout=15
    )

    timeout = client_with_fake_http._http.post.call_args.kwargs["timeout"]
    assert timeout == 15


# Fail-loud paths: 4 RuntimeError


def test_count_features_where_raises_on_http_non_200(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(
        status_code=500,
        content_type="text/html",
        raw_text="<html>Internal Server Error</html>",
    )

    with pytest.raises(RuntimeError, match="HTTP 500"):
        client_with_fake_http.count_features_where(
            conn, "https://arc.test/layer/0", "1=1"
        )


def test_count_features_where_raises_on_non_json_content_type(
    client_with_fake_http, conn,
):
    client_with_fake_http._http.post.return_value = _make_response(
        status_code=200,
        content_type="text/html",
        raw_text="<html>oops</html>",
    )

    with pytest.raises(RuntimeError, match="Risposta non-JSON"):
        client_with_fake_http.count_features_where(
            conn, "https://arc.test/layer/0", "1=1"
        )


def test_count_features_where_raises_on_json_decode_fail(client_with_fake_http, conn):
    client_with_fake_http._http.post.return_value = _make_response(
        status_code=200,
        content_type="application/json",
        raw_text="{not valid json",  # json() solleva ValueError
    )

    with pytest.raises(RuntimeError, match="JSON decode"):
        client_with_fake_http.count_features_where(
            conn, "https://arc.test/layer/0", "1=1"
        )


def test_count_features_where_raises_on_arcgis_error_payload(
    client_with_fake_http, conn,
):
    """Payload `{"error": {...}}` → RuntimeError (ArcGIS REST error)."""
    client_with_fake_http._http.post.return_value = _make_response(
        body={"error": {"code": 400, "message": "Invalid SQL"}},
    )

    with pytest.raises(RuntimeError, match="Errore ArcGIS"):
        client_with_fake_http.count_features_where(
            conn, "https://arc.test/layer/0", "1=1"
        )


# _get_feature_count rimane separato (semantica display-only fail-soft)


def test_get_feature_count_remains_distinct_method():
    """`_get_feature_count` (GET, hardcoded `1=1`, fail-soft Optional[int])
    ha semantica diversa da `count_features_where` (POST, WHERE arbitrario,
    fail-loud int). Resta come metodo separato per i caller display-only
    (`list_layers`, `get_layer_schema`)."""
    assert hasattr(ArcGISClient, "_get_feature_count")
    assert hasattr(ArcGISClient, "count_features_where")
    # Sono due metodi distinti, non alias
    assert ArcGISClient._get_feature_count is not ArcGISClient.count_features_where
