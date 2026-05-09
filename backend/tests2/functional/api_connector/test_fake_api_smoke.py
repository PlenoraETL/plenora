"""
Smoke test del fake server usato dai future functional test di api_connector.

Questi test non esercitano ancora il backend PLENORA completo: verificano che
"""

from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _read_json(url: str, *, method: str = "GET", data: bytes | None = None):
    req = Request(url, method=method, data=data)
    with urlopen(req, timeout=3.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_fake_api_health(fake_api_server):
    payload = _read_json(f"{fake_api_server['base_url']}/health")
    assert payload == {"status": "ok"}


def test_fake_api_json_happy_path(fake_api_server):
    payload = _read_json(f"{fake_api_server['base_url']}/json/ok")
    assert payload["ok"] is True
    assert payload["data"] == [{"value": 1}, {"value": 2}]


def test_fake_api_json_error_contract(fake_api_server):
    try:
        _read_json(f"{fake_api_server['base_url']}/json/error")
    except HTTPError as exc:
        assert exc.code == 422
        body = json.loads(exc.read().decode("utf-8"))
        assert body["detail"] == "invalid payload"
    else:  # pragma: no cover - il test deve fallire se non riceve HTTPError
        raise AssertionError("HTTPError atteso")


def test_fake_api_binary_download(fake_api_server):
    req = Request(f"{fake_api_server['base_url']}/download/bin", method="POST")
    with urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/octet-stream"
        assert resp.read() == b"fake-binary-payload"


def test_fake_api_slow_endpoint(fake_api_server):
    payload = _read_json(f"{fake_api_server['base_url']}/slow?delay_s=0.01")
    assert payload["ok"] is True
    assert payload["delay_s"] == 0.01


def test_fake_api_multipart_echo(fake_api_server, tmp_path):
    boundary = f"boundary-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="note"\r\n\r\n'
        "hello\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="uploaded"; filename="sample.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "payload-123\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    req = Request(
        f"{fake_api_server['base_url']}/multipart/echo",
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))

    assert payload["filename"] == "sample.txt"
    assert payload["content_type"] == "text/plain"
    assert payload["size"] == len(b"payload-123")
    assert payload["note"] == "hello"


def test_fake_api_multipart_fail(fake_api_server):
    req = Request(f"{fake_api_server['base_url']}/multipart/fail", method="POST", data=b"")
    try:
        with urlopen(req, timeout=3.0):
            pass
    except HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read().decode("utf-8"))
        assert body["detail"] == "multipart rejected"
    else:  # pragma: no cover
        raise AssertionError("HTTPError atteso")
