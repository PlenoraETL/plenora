"""
Contract test per la policy di esposizione dei doc endpoint.

Lock di `app.main._compute_doc_endpoints` post centralizzazione canvas
contract (Sprint 2): /openapi.json e' sempre attivo, /docs e /redoc
sono spenti solo in production.

Motivazione (audit cross-modulo): tenere /openapi.json disabled in
prod produce drift FE↔BE non rilevabili. Il FE compila contro uno
schema visto in dev, ma in prod non c'e' nessuna fonte di verita`
condivisa per validator/contract test. Lo schema OpenAPI e'
informazione di shape, non di segreti — equivalente del sorgente
aperto su GitHub.

/docs e /redoc invece restano dev-only: sono UI interattive (Swagger,
Redoc), riducono superficie di reconnaissance senza valore aggiunto
per gli utenti reali (che usano il FE).
"""

from __future__ import annotations

import pytest

from app.main import _compute_doc_endpoints


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.mark.parametrize("env_value", ["production", "PRODUCTION", "prod", "PROD"])
def test_prod_disables_docs_and_redoc(env_value):
    docs, redoc, openapi = _compute_doc_endpoints(env_value)
    assert docs is None
    assert redoc is None
    # /openapi.json resta sempre esposto.
    assert openapi == "/openapi.json"


@pytest.mark.parametrize("env_value", ["development", "DEV", "dev", "staging", None, "", "anything-else"])
def test_non_prod_exposes_all_doc_endpoints(env_value):
    docs, redoc, openapi = _compute_doc_endpoints(env_value)
    assert docs == "/docs"
    assert redoc == "/redoc"
    assert openapi == "/openapi.json"


def test_openapi_endpoint_is_invariant_across_environments():
    """/openapi.json non e' mai None — invariante chiave del fix Sprint 2."""
    for env in ["production", "prod", "development", "staging", None, "garbage"]:
        _, _, openapi = _compute_doc_endpoints(env)
        assert openapi == "/openapi.json", (
            f"/openapi.json deve essere sempre esposto, fallisce per env={env!r}"
        )


def test_openapi_json_actually_served_by_app():
    """Smoke test: l'app risponde 200 con JSON OpenAPI valido."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    # Sanity: campi obbligatori dello schema OpenAPI 3.x.
    assert "openapi" in schema
    assert "info" in schema
    assert "paths" in schema
