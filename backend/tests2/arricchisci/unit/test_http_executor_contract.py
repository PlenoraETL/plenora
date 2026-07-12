"""Contracts for the shared Arricchisci HTTP executor."""

from __future__ import annotations

import pytest

import app.core.arricchisci.http_executor as http_mod
from app.core.arricchisci.http_executor import HttpExecutor, RequestSpec


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


class _StreamingResponse:
    status_code = 200
    headers = {}
    is_redirect = False
    url = "https://api.test/file.bin"

    @property
    def text(self):
        raise AssertionError("stream=True responses must not read .text eagerly")


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _StreamingResponse()


def test_streaming_execute_does_not_read_response_text(monkeypatch):
    session = _Session()
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: session)
    monkeypatch.setattr(http_mod, "validate_external_url", lambda _url: None)

    response = HttpExecutor().execute(
        RequestSpec(url="https://api.test/file.bin", stream=True)
    )

    assert response.text == ""
    assert response.raw_response is not None
    assert session.calls[0][1]["stream"] is True


class _RedirectResponse:
    is_redirect = True
    url = "https://public.example/start"

    def __init__(self, location):
        self.headers = {"location": location}


def test_redirect_target_is_revalidated_before_following(monkeypatch):
    checked = []
    monkeypatch.setattr(http_mod, "validate_external_url", checked.append)

    response = _RedirectResponse("http://169.254.169.254/latest/meta-data")
    assert HttpExecutor._validate_redirect_target(response) is response

    assert checked == ["http://169.254.169.254/latest/meta-data"]


def test_relative_redirect_is_resolved_before_validation(monkeypatch):
    checked = []
    monkeypatch.setattr(http_mod, "validate_external_url", checked.append)

    HttpExecutor._validate_redirect_target(_RedirectResponse("/next"))

    assert checked == ["https://public.example/next"]


def test_redirect_validation_hook_is_installed_only_when_enabled(monkeypatch):
    session = _Session()
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: session)
    monkeypatch.setattr(http_mod, "validate_external_url", lambda _url: None)

    HttpExecutor().execute(RequestSpec(
        url="https://api.test/file.bin",
        stream=True,
        allow_redirects=True,
    ))

    hook = session.calls[0][1]["hooks"]["response"]
    assert hook == HttpExecutor._validate_redirect_target
