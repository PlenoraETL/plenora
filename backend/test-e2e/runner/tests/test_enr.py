"""ENR-001..ENR-XXX — Arricchisci smokes (REST/API enrichment).

Catalogo: backend/test-e2e/catalog/enr.yaml
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import pytest


API_PREFIX = "/api"


def _poll_task(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    task_id: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    terminal = {
        "success", "completed", "failure", "failed", "error", "cancelled",
    }
    while time.monotonic() < deadline:
        r = http_client.get(
            f"{API_PREFIX}/tasks/{task_id}", headers=auth_headers,
        )
        if r.status_code == 200:
            body = r.json()
            if (body.get("status") or "").lower() in terminal:
                return body
        time.sleep(1)
    raise AssertionError(f"Task {task_id} non terminato in {timeout}s")


# ── ENR-001 — OAuth2 client_credentials lookup ────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_enr_001_oauth2_client_credentials(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    """ENR-001 — Lookup OAuth2 client_credentials.

    Catalog `enr.yaml`: dataset
    `fake-rest:8080/auth/oauth2/protected`. Plenora deve negoziare
    il token tramite POST `/auth/oauth2/token` (grant_type=
    client_credentials) e poi usarlo come Bearer per chiamare
    l'endpoint protetto.

    Pattern Plenora supportato:
      auth = {
        "type": "oauth2_client_credentials",
        "token_url": "<base>/auth/oauth2/token",
        "client_id": "plenora",
        "client_secret": "plenora_secret",
      }

    Il fake-rest accetta solo `client_id=plenora` /
    `client_secret=plenora_secret` e ritorna `OAUTH_FAKE_TOKEN`
    (vedi `fake-rest/README.md`). L'endpoint protetto richiede
    quel token come Bearer e ritorna 200 + JSON.

    Assertion catalog: "token negotiated, riusato per richieste
    successive". Verifichiamo via il `test` endpoint by-id, che
    scarica il token via `get_cached_oauth2_token` e poi fa la
    GET protetta. Cache: la seconda chiamata a `test` riusa il
    token finche' non scade — non lo verifichiamo qui (richiede
    introspezione cache), ci accontentiamo dei due test sequenziali
    che ritornano success entrambi.
    """
    base = os.getenv(
        "PLENORA_E2E_FAKE_REST_URL", "http://plenora-e2e-fake-rest:8080"
    )
    payload = {
        "name": f"e2e-enr001-{uuid.uuid4().hex[:6]}",
        "description": "ENR-001 OAuth2 client_credentials",
        "connection_type": "rest",
        "api_type": "enricher",
        "url": f"{base}/auth/oauth2/protected",
        "method": "GET",
        "auth": {
            "type": "oauth2_client_credentials",
            "token_url": f"{base}/auth/oauth2/token",
            "client_id": os.getenv(
                "PLENORA_E2E_OAUTH2_CLIENT_ID", "plenora"
            ),
            "client_secret": os.getenv(
                "PLENORA_E2E_OAUTH2_CLIENT_SECRET", "plenora_secret"
            ),
        },
    }
    r_create = http_client.post(
        f"{API_PREFIX}/api-connections",
        json=payload, headers=auth_headers,
    )
    assert r_create.status_code in (200, 201), (
        f"create api-connection oauth2: {r_create.status_code} — "
        f"{r_create.text[:200]}"
    )
    cid = r_create.json().get("id")
    assert cid, f"create senza id: {r_create.json()}"

    try:
        # 1. Test connection — negotiate token + GET protected
        r_test = http_client.post(
            f"{API_PREFIX}/api-connections/{cid}/test",
            json={}, headers=auth_headers,
        )
        assert r_test.status_code == 200, (
            f"test oauth2: {r_test.status_code} — {r_test.text[:200]}"
        )
        body = r_test.json()
        assert body.get("success") is True, (
            f"ENR-001 test fallito: {body}"
        )
        assert body.get("status_code") == 200, (
            f"ENR-001 oauth2 protected non 200: {body}"
        )

        # 2. Seconda chiamata: il token deve essere riusato dalla
        # cache (non rifatto). Verifichiamo solo che continui a
        # ritornare success — la cache hit/miss e' isolata nel
        # backend, qui controlliamo l'effetto end-to-end.
        r_test2 = http_client.post(
            f"{API_PREFIX}/api-connections/{cid}/test",
            json={}, headers=auth_headers,
        )
        assert r_test2.status_code == 200, (
            f"test oauth2 #2: {r_test2.status_code} — {r_test2.text[:200]}"
        )
        assert r_test2.json().get("success") is True, (
            f"ENR-001 second test fallito: {r_test2.json()}"
        )
    finally:
        http_client.delete(
            f"{API_PREFIX}/api-connections/{cid}", headers=auth_headers,
        )
