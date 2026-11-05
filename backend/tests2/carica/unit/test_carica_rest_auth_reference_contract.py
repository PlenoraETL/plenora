"""Credenziali CARICA REST per riferimento al connettore API (audit frontend R10.8).

Il canvas porta solo ``rest_auth_api_id``; il server risolve
l'autenticazione del connettore, solo se visibile al gruppo del progetto, e
la manda come intestazioni segrete.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.carica.rest_auth import (
    RestAuthUnavailable,
    resolve_rest_auth_headers,
    with_auth_headers,
)
from app.core.connections.rest_sink import RestSinkConfig, RestSinkHeader, RestSinkHttpResponse


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]

CONNECTOR = {"id": "api-1", "group_id": "g1", "auth": {"type": "bearer", "token": "tok-123"}}


def _resolve(node, group="g1", visible=True, connector=CONNECTOR):
    return resolve_rest_auth_headers(
        node,
        group,
        load_connection=lambda api_id: connector,
        visible=lambda api_id, group_id: visible,
    )


def test_no_reference_means_no_auth_headers():
    assert _resolve({"dest_type": "rest"}) == ()


def test_reference_becomes_secret_authorization_header():
    headers = _resolve({"rest_auth_api_id": "api-1"})
    assert headers == (RestSinkHeader(name="Authorization", value="Bearer tok-123", secret=True),)


def test_connector_not_visible_to_project_group_fails_closed():
    with pytest.raises(RestAuthUnavailable):
        _resolve({"rest_auth_api_id": "api-1"}, visible=False)
    with pytest.raises(RestAuthUnavailable):
        _resolve({"rest_auth_api_id": "api-1"}, group="")
    with pytest.raises(RestAuthUnavailable):
        _resolve({"rest_auth_api_id": "api-1"}, connector=None)


def test_connector_headers_replace_node_headers_with_same_name():
    config = RestSinkConfig(
        url="https://api.example.com/items",
        headers=(RestSinkHeader(name="authorization", value="vecchio"), RestSinkHeader(name="X-Client", value="p")),
    )
    merged = with_auth_headers(config, (RestSinkHeader(name="Authorization", value="Bearer t", secret=True),))
    assert [(h.name, h.value, h.secret) for h in merged.headers] == [
        ("X-Client", "p", False),
        ("Authorization", "Bearer t", True),
    ]


def test_rest_write_sends_connector_credentials(monkeypatch):
    from app.core.carica import dispatch

    sent = {}

    def request(**kwargs):
        sent.update(kwargs)
        return RestSinkHttpResponse(status_code=201, body={}, text="")

    monkeypatch.setattr(dispatch, "create_default_rest_http_client", lambda: SimpleNamespace(request=MagicMock(side_effect=request)))
    monkeypatch.setattr(
        dispatch,
        "resolve_rest_auth_headers",
        lambda node, group_id: (RestSinkHeader(name="Authorization", value="Bearer tok", secret=True),) if group_id == "g1" else (),
    )
    df = pd.DataFrame([{"id": 1}])
    node = {
        "dest_type": "rest",
        "rest_url": "https://api.example.com/items",
        "rest_auth_api_id": "api-1",
        "rest_success_statuses": [201],
    }
    result = dispatch.execute_rest_write(df, node, "p1", "t1", progress=lambda *a: None, expected_group_id="g1")
    assert result.get("success") is True
    assert sent["headers"]["Authorization"] == "Bearer tok"
    assert "Bearer tok" not in repr(result)


def test_rest_write_stops_when_connector_unavailable(monkeypatch):
    from app.core.carica import dispatch

    def unavailable(node, group_id):
        raise RestAuthUnavailable("Connettore API di autenticazione non visibile al gruppo del progetto")

    monkeypatch.setattr(dispatch, "resolve_rest_auth_headers", unavailable)
    client = SimpleNamespace(request=MagicMock())
    monkeypatch.setattr(dispatch, "create_default_rest_http_client", lambda: client)
    df = pd.DataFrame([{"id": 1}])
    node = {"dest_type": "rest", "rest_url": "https://api.example.com/items", "rest_auth_api_id": "api-x"}
    result = dispatch.execute_rest_write(df, node, "p1", "t1", progress=lambda *a: None, expected_group_id="g1")
    assert result["success"] is False
    assert result["error"] == "carica_rest_auth_unavailable"
    client.request.assert_not_called()
