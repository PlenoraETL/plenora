"""IDEM-001..IDEM-XXX — Idempotency smokes.

Catalogo: backend/test-e2e/catalog/idem.yaml
"""

from __future__ import annotations

import uuid

import httpx
import pytest


API_PREFIX = "/api"


# ── IDEM-005 — DELETE doppio è idempotente ────────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
@pytest.mark.parametrize(
    "factory",
    [
        "connection",
        "datasource",
        "project",
        "api_connection",
    ],
)
def test_idem_005_delete_double_is_consistent(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    factory: str,
) -> None:
    """IDEM-005 — Una DELETE su una risorsa già cancellata deve
    rispondere in modo deterministico (404), non 200/204 silenzioso
    né 500. Catalog `idem.yaml`: "prima 204; seconda 404
    (consistente, documentato in spec API)".

    Plenora ha quattro famiglie di DELETE shareable: connections,
    datasources, projects, api_connections. Verifichiamo tutte e
    quattro per coprire eventuali divergenze tra i router (alcuni
    usano `_disclosure.raise_disclosure` con 404, altri 403; il
    contratto deve restare consistente).

    Step:
      1. crea risorsa
      2. DELETE -> 204 (eventualmente 200 con body)
      3. DELETE di nuovo -> 404 (mai 204/200, mai 500)
    """
    # 1. Crea risorsa secondo il factory
    if factory == "connection":
        payload = {
            "name": f"e2e-idem-conn-{uuid.uuid4().hex[:6]}",
            "description": "IDEM-005 connection",
            "type": "postgres",
            "host": "plenora-e2e-postgres",
            "port": 5432,
            "database": "plenora_test",
            "username": "plenora",
            "password": "plenora_test_pwd",
        }
        path_collection = f"{API_PREFIX}/connections"
    elif factory == "datasource":
        payload = {
            "name": f"e2e-idem-ds-{uuid.uuid4().hex[:6]}",
            "description": "IDEM-005 datasource",
            "type": "ftp",
            "host": "plenora-e2e-ftp",
            "port": 21,
            "username": "tester",
            "password": "plenora_ftp_pwd",
            "root_path": "/home/tester/fixtures",
            "passive": True,
            "tls": False,
        }
        path_collection = f"{API_PREFIX}/datasources"
    elif factory == "project":
        payload = {
            "name": f"e2e-idem-proj-{uuid.uuid4().hex[:6]}",
            "description": "IDEM-005 project",
            "project_type": "manipola",
        }
        path_collection = f"{API_PREFIX}/projects"
    else:  # api_connection
        payload = {
            "name": f"e2e-idem-api-{uuid.uuid4().hex[:6]}",
            "description": "IDEM-005 api_connection",
            "connection_type": "rest",
            "api_type": "enricher",
            "url": "http://plenora-e2e-fake-rest:8080/data/object_root",
            "method": "GET",
            "auth": {"type": "none"},
        }
        path_collection = f"{API_PREFIX}/api-connections"

    r_create = http_client.post(
        path_collection, json=payload, headers=auth_headers,
    )
    assert r_create.status_code in (200, 201), (
        f"IDEM-005[{factory}] create: {r_create.status_code} — "
        f"{r_create.text[:200]}"
    )
    body = r_create.json()
    rid = (body.get("metadata") or {}).get("id") or body.get("id")
    assert rid, f"IDEM-005[{factory}] create senza id: {body}"

    target_url = f"{path_collection}/{rid}"

    # 2. Prima DELETE -> 204 / 200
    r_del1 = http_client.delete(target_url, headers=auth_headers)
    assert r_del1.status_code in (200, 204), (
        f"IDEM-005[{factory}] prima DELETE: atteso 204/200, "
        f"ottenuto {r_del1.status_code} — {r_del1.text[:200]}"
    )

    # 3. Seconda DELETE -> 404 deterministico, mai 5xx, mai 204
    r_del2 = http_client.delete(target_url, headers=auth_headers)
    assert r_del2.status_code == 404, (
        f"IDEM-005[{factory}] seconda DELETE: atteso 404, ottenuto "
        f"{r_del2.status_code} — {r_del2.text[:200]}. La spec del "
        f"contratto richiede 404 deterministico per DELETE doppio "
        f"(no 204 silenzioso, no 5xx)."
    )
