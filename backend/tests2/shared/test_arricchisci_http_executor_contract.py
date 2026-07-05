"""Contract tests for the shared Arricchisci HTTP executor."""

from __future__ import annotations

import pytest

import app.core.arricchisci.http_executor as http_mod
from app.core.arricchisci.http_executor import (
    RequestSpec,
    execute_http_request,
    timeout_from_options,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
]


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self) -> None:
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _Response()

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response()


def _install(monkeypatch):
    session = _Session()
    validated = []
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: session)
    monkeypatch.setattr(http_mod, "validate_external_url", lambda url: validated.append(url))
    return session, validated


def test_get_uses_query_params_and_disables_redirects_by_default(monkeypatch):
    session, validated = _install(monkeypatch)

    response = execute_http_request(RequestSpec(
        url="https://api.example.com/items",
        method="GET",
        params={"q": "roma"},
        headers={"X-Test": "1"},
        timeout=(2, 5),
    ))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert validated == ["https://api.example.com/items"]
    assert session.post_calls == []
    url, kwargs = session.get_calls[0]
    assert url == "https://api.example.com/items"
    assert kwargs["params"] == {"q": "roma"}
    assert kwargs["headers"] == {"X-Test": "1"}
    assert kwargs["timeout"] == (2, 5)
    assert kwargs["allow_redirects"] is False
    assert response.headers.get("content-type") == "application/json"
    assert response.headers.get("Content-Type") == "application/json"


def test_non_get_methods_preserve_phase0_post_json_compat(monkeypatch):
    session, _validated = _install(monkeypatch)

    execute_http_request(RequestSpec(
        url="https://api.example.com/items/1",
        method="PATCH",
        json_body={"name": "updated"},
    ))

    assert session.get_calls == []
    url, kwargs = session.post_calls[0]
    assert url == "https://api.example.com/items/1"
    assert kwargs["json"] == {"name": "updated"}


def test_validate_url_can_be_disabled_for_prevalidated_callers(monkeypatch):
    session = _Session()
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: session)

    def _boom(_url):
        raise AssertionError("validator should not be called")

    monkeypatch.setattr(http_mod, "validate_external_url", _boom)

    execute_http_request(RequestSpec(
        url="https://api.example.com/items",
        validate_url=False,
    ))

    assert len(session.get_calls) == 1


def test_timeout_from_options_keeps_default_when_absent():
    assert timeout_from_options({}, default_read_timeout=30) == 30


def test_timeout_from_options_uses_compat_timeout_field():
    assert timeout_from_options({"timeout": 45}, default_read_timeout=30) == 45


def test_timeout_from_options_supports_split_connect_and_read():
    assert timeout_from_options(
        {"connect_timeout": 2, "request_timeout": 10},
        default_read_timeout=30,
    ) == (2, 10)
