"""Proxy delle specifiche OpenAPI (Fase 5, P10).

Il proxy non interpreta la specifica: restituisce il JSON gia' letto oppure il
testo (YAML) decodificato dai byte con una regola sola (BOM, charset
dichiarato, UTF-8), che il frontend normalizza con l'unico importatore
(``frontend/src/utils/openapiImport.js``). Prima una specifica YAML scaricata
da URL finiva in un 502; un text/yaml senza charset diventava ISO-8859-1.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from requests.structures import CaseInsensitiveDict

from app.core.arricchisci.router import api_connections_router, decode_spec_bytes
from app.core.auth.auth import require_not_esecutore
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]

YAML = 'openapi: 3.0.0\ninfo:\n  title: Anagrafe città perché è\n  version: "1"\npaths: {}\n'
JSON_DOC = '{"openapi": "3.0.0", "info": {"title": "Città"}, "paths": {}}'

# Ogni Content-Type con cui un server puo' servire una specifica: la
# decodifica parte dai BYTE e non dipende dal tipo.
CONTENT_TYPES = [
    None, "application/json", "application/yaml", "application/x-yaml", "text/yaml",
    "text/x-yaml", "text/plain", "application/octet-stream", "application/vnd.oai.openapi",
    "text/plain; charset=utf-8", 'text/yaml; charset="UTF-8"',
]


def _response(content: bytes, status: int = 200, content_type: str | None = "application/json", headers=None):
    """Risposta con BYTE veri (come requests.Response.content), non testo gia' decodificato."""
    h = CaseInsensitiveDict(headers or {})
    if content_type is not None:
        h["Content-Type"] = content_type
    return SimpleNamespace(status_code=status, content=content, headers=h)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: CurrentUser(
        user_id="u-1", username="tester", group_id="grp-1", role="admin")
    app.include_router(api_connections_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def _post(client, resp):
    with patch("app.core.arricchisci.router.request_pinned", return_value=resp) as pinned:
        response = client.post("/api/api-connections/fetch-openapi-schema", json={"url": "https://x.example/spec"})
    return response, pinned


@pytest.mark.parametrize("content_type", CONTENT_TYPES)
@pytest.mark.parametrize("text, expected", [
    (JSON_DOC, {"openapi": "3.0.0", "info": {"title": "Città"}, "paths": {}}),
    (YAML, YAML),
])
def test_proxy_decodes_utf8_bytes_for_every_content_type(client, content_type, text, expected):
    response, pinned = _post(client, _response(text.encode("utf-8"), content_type=content_type))
    assert response.status_code == 200
    assert response.json() == expected
    accept = pinned.call_args.kwargs["headers"]["Accept"]
    assert "application/json" in accept and "yaml" in accept


@pytest.mark.parametrize("raw, content_type", [
    (YAML.encode("utf-8-sig"), "text/plain"),
    (YAML.encode("utf-8-sig"), "text/plain; charset=ISO-8859-1"),  # il BOM vince
    (YAML.encode("utf-16"), "text/yaml"),
    (YAML.encode("utf-32"), None),
    (YAML.encode("utf-8"), "text/yaml; charset=utf-8"),
    ('openapi: 3.0.0\ninfo: {title: café}\n'.encode("latin-1"), "text/yaml; charset=ISO-8859-1"),
    ('openapi: 3.0.0\ninfo: {title: café €}\n'.encode("cp1252"), "text/plain; charset=windows-1252"),
])
def test_bom_or_declared_charset_decides(client, raw, content_type):
    expected, replaced = decode_spec_bytes(raw, content_type)
    assert replaced is False and "�" not in expected and "Ã" not in expected
    response, _ = _post(client, _response(raw, content_type=content_type))
    assert response.status_code == 200
    assert response.json() == expected
    assert response.json().lstrip("﻿").startswith("openapi: 3.0.0")


@pytest.mark.parametrize("content_type", ["text/yaml", "text/yaml; charset=nonesiste", "text/yaml; charset=utf-8"])
def test_undecodable_bytes_fall_back_to_utf8_with_replacement(client, content_type):
    raw = b"openapi: 3.0.0\ninfo: {title: caf\xe9}\n"
    text, replaced = decode_spec_bytes(raw, content_type)
    assert replaced is True and "caf�" in text
    response, _ = _post(client, _response(raw, content_type=content_type))
    assert response.status_code == 200
    assert "caf�" in response.json()


def test_decode_reports_clean_utf8():
    assert decode_spec_bytes(YAML.encode("utf-8"), "text/plain") == (YAML, False)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_an_error_with_the_final_url(client, status):
    resp = _response(b"", status=status, headers={"Location": "https://y.example/openapi.yaml"})
    response, _ = _post(client, resp)
    assert response.status_code == 502
    message = response.json()["detail"]["message"]
    assert f"HTTP {status}" in message and "https://y.example/openapi.yaml" in message
    assert "usa l'URL finale" in message


def test_proxy_keeps_upstream_http_error(client):
    response, _ = _post(client, _response(b"nope", 404))
    assert response.status_code == 404
