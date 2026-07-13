"""Contracts for the shared Arricchisci HTTP executor."""

from __future__ import annotations

import pytest

import app.core.arricchisci.http_executor as http_mod
from app.core.arricchisci.http_executor import HttpExecutor, RequestSpec
from app.core.security.outbound_url import ResolvedOutboundTarget


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
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [_StreamingResponse()])

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _resolved(url):
    path = url.split("/", 3)[-1] if "/" in url[8:] else ""
    return ResolvedOutboundTarget(
        original_url=url,
        pinned_url=f"https://203.0.113.10/{path}",
        hostname="api.test",
        host_header="api.test",
    )


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
    assert session.calls[0][0] == "https://api.test/file.bin"
    assert session.calls[0][1]["allow_redirects"] is False


class _RedirectResponse:
    is_redirect = True
    url = "https://public.example/start"

    status_code = 302

    def __init__(self, location):
        self.headers = {"location": location}

    @property
    def text(self):
        return ""


def test_same_host_redirect_is_manually_revalidated(monkeypatch):
    checked = []
    final = _StreamingResponse()
    session = _Session([_RedirectResponse("/next"), final])
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: session)
    monkeypatch.setattr(http_mod, "validate_external_url", checked.append)

    response = HttpExecutor().execute(RequestSpec(
        url="https://api.test/start",
        stream=True,
        allow_redirects=True,
    ))

    assert response.raw_response is final
    assert checked == ["https://api.test/start", "https://api.test/next"]
    assert len(session.calls) == 2


def test_cross_host_redirect_is_rejected(monkeypatch):
    session = _Session([_RedirectResponse("https://other.test/next")])
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: session)
    monkeypatch.setattr(http_mod, "validate_external_url", lambda _url: None)

    with pytest.raises(ValueError, match="cross-host"):
        HttpExecutor().execute(RequestSpec(
            url="https://api.test/start",
            stream=True,
            allow_redirects=True,
        ))


def test_requests_session_branch_connects_to_pinned_ip(monkeypatch):
    import requests

    base_session = requests.Session()
    pinned_session = _Session()
    monkeypatch.setattr(http_mod, "get_shared_session", lambda: base_session)
    monkeypatch.setattr(http_mod, "resolve_external_url", _resolved)
    monkeypatch.setattr(
        http_mod,
        "build_pinned_session",
        lambda *_args, **_kwargs: pinned_session,
    )

    HttpExecutor().execute(RequestSpec(
        url="https://api.test/file.bin",
        stream=True,
    ))

    assert pinned_session.calls[0][0] == "https://203.0.113.10/file.bin"
    assert pinned_session.calls[0][1]["headers"]["Host"] == "api.test"
