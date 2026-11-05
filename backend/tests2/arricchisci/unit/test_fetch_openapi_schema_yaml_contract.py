"""Proxy delle specifiche OpenAPI (Fase 5, P10).

Il proxy non interpreta la specifica: restituisce il JSON gia' letto oppure il
testo cosi' com'e' (YAML), che il frontend normalizza con l'unico importatore
(``frontend/src/utils/openapiImport.js``). Prima una specifica YAML scaricata
da URL finiva in un 502.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.arricchisci.router import api_connections_router
from app.core.auth.auth import require_not_esecutore
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]

YAML = 'openapi: 3.0.0\ninfo:\n  title: X\n  version: "1"\npaths: {}\n'


def _response(text: str, status: int = 200):
    def _json():
        return requests.models.complexjson.loads(text)
    return SimpleNamespace(status_code=status, text=text, json=_json)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: CurrentUser(
        user_id="u-1", username="tester", group_id="grp-1", role="admin")
    app.include_router(api_connections_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("body, expected", [
    ('{"openapi": "3.0.0", "paths": {}}', {"openapi": "3.0.0", "paths": {}}),
    (YAML, YAML),
])
def test_proxy_returns_json_document_or_raw_text(client, body, expected):
    with patch("app.core.arricchisci.router.request_pinned", return_value=_response(body)) as pinned:
        response = client.post("/api/api-connections/fetch-openapi-schema", json={"url": "https://x.example/spec"})
    assert response.status_code == 200
    assert response.json() == expected
    accept = pinned.call_args.kwargs["headers"]["Accept"]
    assert "application/json" in accept and "yaml" in accept


def test_proxy_keeps_upstream_http_error(client):
    with patch("app.core.arricchisci.router.request_pinned", return_value=_response("nope", 404)):
        response = client.post("/api/api-connections/fetch-openapi-schema", json={"url": "https://x.example/spec"})
    assert response.status_code == 404
