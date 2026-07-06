"""Contract tests for the shared Arricchisci HTTP executor."""

from __future__ import annotations

import pytest

import app.core.arricchisci.http_executor as http_mod
from app.core.arricchisci.http_executor import (
    RequestSpec,
    allow_redirects_from_config,
    body_type_from_config,
    execute_http_request,
    raw_body_from_config,
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
        self.put_calls = []
        self.patch_calls = []
        self.delete_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _Response()

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response()

    def put(self, url, **kwargs):
        self.put_calls.append((url, kwargs))
        return _Response()

    def patch(self, url, **kwargs):
        self.patch_calls.append((url, kwargs))
        return _Response()

    def delete(self, url, **kwargs):
        self.delete_calls.append((url, kwargs))
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


def test_patch_dispatches_to_patch_with_json_body(monkeypatch):
    session, _validated = _install(monkeypatch)

    execute_http_request(RequestSpec(
        url="https://api.example.com/items/1",
        method="PATCH",
        json_body={"name": "updated"},
    ))

    assert session.get_calls == []
    assert session.post_calls == []
    url, kwargs = session.patch_calls[0]
    assert url == "https://api.example.com/items/1"
    assert kwargs["json"] == {"name": "updated"}


def test_put_dispatches_to_put(monkeypatch):
    session, _validated = _install(monkeypatch)

    execute_http_request(RequestSpec(
        url="https://api.example.com/items/1",
        method="PUT",
        json_body={"name": "updated"},
    ))

    assert len(session.put_calls) == 1


def test_delete_dispatches_to_delete(monkeypatch):
    session, _validated = _install(monkeypatch)

    execute_http_request(RequestSpec(
        url="https://api.example.com/items/1",
        method="DELETE",
        body_type="none",
        params={"hard": "1"},
    ))

    url, kwargs = session.delete_calls[0]
    assert url == "https://api.example.com/items/1"
    assert kwargs["params"] == {"hard": "1"}


def test_form_urlencoded_body_uses_data(monkeypatch):
    session, _validated = _install(monkeypatch)

    execute_http_request(RequestSpec(
        url="https://api.example.com/token",
        method="POST",
        body_type="form-urlencoded",
        json_body={"grant_type": "client_credentials"},
    ))

    _url, kwargs = session.post_calls[0]
    assert kwargs["data"] == {"grant_type": "client_credentials"}
    assert "json" not in kwargs


def test_raw_body_uses_data(monkeypatch):
    session, _validated = _install(monkeypatch)

    execute_http_request(RequestSpec(
        url="https://api.example.com/query",
        method="POST",
        body_type="raw/text",
        raw_body="select * from t",
    ))

    _url, kwargs = session.post_calls[0]
    assert kwargs["data"] == "select * from t"
    assert "json" not in kwargs


def test_unsupported_method_raises_value_error(monkeypatch):
    _session, _validated = _install(monkeypatch)

    with pytest.raises(ValueError, match="Metodo HTTP non supportato"):
        execute_http_request(RequestSpec(
            url="https://api.example.com/items",
            method="HEAD",
        ))


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


def test_request_config_helpers_normalize_body_and_redirects():
    request_cfg = {
        "body_type": "raw_text",
        "raw_body": "hello",
        "allow_redirects": True,
    }

    assert body_type_from_config(request_cfg) == "raw"
    assert raw_body_from_config(request_cfg) == "hello"
    assert allow_redirects_from_config(
        {"allow_redirects": False},
        request_cfg,
    ) is True


def test_options_allow_redirects_used_when_request_omits_it():
    assert allow_redirects_from_config(
        {"allow_redirects": True},
        {},
    ) is True


def test_allow_redirects_string_false_is_false():
    assert allow_redirects_from_config(
        {},
        {"allow_redirects": "false"},
    ) is False
