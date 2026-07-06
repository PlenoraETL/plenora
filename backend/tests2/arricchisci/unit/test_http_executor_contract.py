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
