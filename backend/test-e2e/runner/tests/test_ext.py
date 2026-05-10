"""EXT-001..EXT-030 — Estrai (sources E2E).

Catalogo: backend/test-e2e/catalog/ext.yaml
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Iterator

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
    """Polla `GET /tasks/{id}` fino a stato terminale o timeout."""
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


# ── EXT-001 — Estrai tutto da PostgreSQL/PostGIS ───────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_ext_001_estrai_postgres_customers(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    project_id: str,
    postgres_conn_id: str,
) -> None:
    """EXT-001 — Estrazione completa della tabella `customers`
    (22 righe nel seed `sql/postgres/01_seed.sql`) da
    `plenora-e2e-postgres`.

    Steps (catalog `ext.yaml`):
      1) connection postgres → fixture `postgres_conn_id` la riusa
      2) canvas con un nodo `source` che punta a `customers`
      3) execute + poll
      4) preview risultato → 22 righe attese
    """
    # 1+2. Plenora `Node` (canvas_models.py:80) ha i campi a top-level:
    # `table_name`, `connection_id`, `columns` NON dentro `data`. Il
    # backend Pydantic li ignora se annidati. Inoltre serve un nodo
    # `result` come sink (senza, "Nodo risultato non trovato") e
    # `columns` esplicite (senza, "Nessuna colonna selezionata").
    src_id = f"src_customers_{uuid.uuid4().hex[:6]}"
    res_id = f"res_{uuid.uuid4().hex[:6]}"
    canvas = {
        "nodes": [
            {
                "id": src_id,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": postgres_conn_id,
                "table_name": "customers",
                "columns": [
                    {"name": "id", "type": "int", "visible": True},
                    {"name": "city_id", "type": "int", "visible": True},
                    {"name": "full_name", "type": "str", "visible": True},
                    {"name": "email", "type": "str", "visible": True},
                    {"name": "age", "type": "int", "visible": True},
                    {"name": "signup_date", "type": "date", "visible": True},
                    {"name": "status", "type": "str", "visible": True},
                ],
            },
            {
                "id": res_id,
                "type": "result",
                "position": {"x": 400, "y": 100},
            },
        ],
        "edges": [
            {
                "id": f"e_{uuid.uuid4().hex[:6]}",
                "source": src_id,
                "target": res_id,
            },
        ],
    }
    r_put = http_client.put(
        f"{API_PREFIX}/projects/{project_id}/canvas",
        json=canvas, headers=auth_headers,
    )
    assert r_put.status_code == 200, (
        f"PUT canvas fallito: HTTP {r_put.status_code} — {r_put.text[:300]}"
    )

    # 3. Execute
    r_exec = http_client.post(
        f"{API_PREFIX}/projects/{project_id}/execute",
        json={}, headers=auth_headers,
    )
    assert r_exec.status_code in (200, 201, 202), (
        f"execute non success: HTTP {r_exec.status_code} — {r_exec.text[:300]}"
    )
    body = r_exec.json()
    task_id = body.get("task_id") or body.get("id")
    assert task_id, f"execute senza task_id: {body}"

    # Poll
    deadline = time.monotonic() + 60
    final: dict[str, Any] | None = None
    terminal = {"success", "completed", "failure", "failed", "error", "cancelled"}
    while time.monotonic() < deadline:
        r_t = http_client.get(
            f"{API_PREFIX}/tasks/{task_id}", headers=auth_headers,
        )
        if r_t.status_code == 200:
            tb = r_t.json()
            if (tb.get("status") or "").lower() in terminal:
                final = tb
                break
        time.sleep(1)

    assert final is not None, f"Task {task_id} non terminato in 60s"
    status = (final.get("status") or "").lower()
    assert status in {"success", "completed"}, (
        f"Task non success: status={status!r}, body={final}"
    )

    # 4. Verifica numero righe
    # Plenora ha shape eterogenea: result.rows | result.row_count | rows_count.
    # Accettiamo qualunque chiave canonica purche' sia 22.
    result = final.get("result") or {}
    candidates = (
        result.get("rows"),
        result.get("row_count"),
        result.get("rows_count"),
        result.get("count"),
        final.get("rows"),
        final.get("row_count"),
    )
    rows_value = next(
        (v for v in candidates if isinstance(v, int)), None,
    )
    assert rows_value == 22, (
        f"EXT-001 atteso 22 righe, ottenuto rows_value={rows_value!r}. "
        f"Final task: {final}. Result: {result}"
    )


# ── EXT-021 — Estrai REST con api_key in query ────────────────────────


@pytest.fixture(scope="function")
def api_conn_apikey_query_id(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
) -> Iterator[str]:
    """api_connection REST con api_key in **query string**.

    Distinta dalla `api_conn_apikey_id` di session-scope che usa
    `location=header`. EXT-021 richiede esplicitamente la variante
    query; teniamola function-scoped per non interferire con altri
    test che usano l'header variant.
    """
    base_url = os.getenv(
        "PLENORA_E2E_FAKE_REST_URL", "http://plenora-e2e-fake-rest:8080"
    )
    payload = {
        "name": f"e2e-rest-apikey-query-{uuid.uuid4().hex[:6]}",
        "description": "EXT-021 fixture: api_key in query string",
        "connection_type": "rest",
        # api_type=generator: 1 chiamata HTTP totale, response espansa
        # in N righe (estrai REST). enricher invece fa 1 chiamata per
        # riga input, qui non e' quello che vogliamo.
        "api_type": "generator",
        "url": f"{base_url}/data/object_root",
        "method": "GET",
        "auth": {
            "type": "api_key",
            "location": "query",
            "key_name": "api_key",
            "key_value": os.getenv(
                "PLENORA_E2E_FAKE_REST_KEY", "PLENORA_TEST_KEY"
            ),
        },
    }
    r = http_client.post(
        f"{API_PREFIX}/api-connections", json=payload, headers=auth_headers,
    )
    assert r.status_code in (200, 201), (
        f"Setup api_conn (apikey query) fallito: HTTP {r.status_code} — "
        f"{r.text[:200]}"
    )
    body = r.json()
    cid = body.get("id") or body.get("api_id") or body.get("api_connection_id")
    assert cid, f"Risposta senza id: {body}"
    yield str(cid)
    http_client.delete(
        f"{API_PREFIX}/api-connections/{cid}", headers=auth_headers,
    )


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_ext_021_estrai_rest_apikey_query(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    api_conn_apikey_query_id: str,
) -> None:
    """EXT-021 — Estrazione REST con `api_key` in query string.

    Endpoint: `fake-rest:8080/data/object_root?api_key=PLENORA_TEST_KEY`
    Atteso: 12 customer (catalog `ext.yaml`).

    Workflow Plenora (interpretazione A confermata da utente):
      - project tipo `arricchisci`
      - canvas con 1 nodo `api_enrich` come "estrai REST" + result sink
      - il nodo punta all'api_connection REST e legge `data` JSON path
    """
    base_url = os.getenv(
        "PLENORA_E2E_FAKE_REST_URL", "http://plenora-e2e-fake-rest:8080"
    )

    # 1. Crea project tipo arricchisci
    proj_payload = {
        "name": f"e2e-ext021-{uuid.uuid4().hex[:6]}",
        "description": "EXT-021: estrai REST api_key query",
        "project_type": "arricchisci",
    }
    r_proj = http_client.post(
        f"{API_PREFIX}/projects", json=proj_payload, headers=auth_headers,
    )
    assert r_proj.status_code in (200, 201), (
        f"POST project fallito: {r_proj.status_code} — {r_proj.text[:200]}"
    )
    pj = r_proj.json()
    project_id = (pj.get("metadata") or {}).get("id") or pj.get("id")
    assert project_id, f"project response senza id: {pj}"

    try:
        # 2. PUT canvas: api_enrich(generator) standalone -> result.
        # `api_type=generator` rende il nodo source-like (vedi
        # `projects/router.py:_is_source_like`); il runtime chiama
        # `_execute_api_generator` con input_df=None e usa la response
        # come dataframe sorgente.
        rest_id = f"rest_{uuid.uuid4().hex[:6]}"
        res_id = f"res_{uuid.uuid4().hex[:6]}"
        canvas = {
            "nodes": [
                {
                    "id": rest_id,
                    "type": "api_enrich",
                    "position": {"x": 100, "y": 100},
                    "api_id": api_conn_apikey_query_id,
                    "api_type": "generator",
                    "endpoint": {
                        "url": f"{base_url}/data/object_root",
                        "method": "GET",
                    },
                    "response": {"json_path": "$.data"},
                },
                {
                    "id": res_id,
                    "type": "result",
                    "position": {"x": 400, "y": 100},
                },
            ],
            "edges": [
                {
                    "id": f"e_rest_res_{uuid.uuid4().hex[:4]}",
                    "source": rest_id,
                    "target": res_id,
                },
            ],
        }
        r_put = http_client.put(
            f"{API_PREFIX}/projects/{project_id}/canvas",
            json=canvas, headers=auth_headers,
        )
        assert r_put.status_code == 200, (
            f"PUT canvas fallito: {r_put.status_code} — {r_put.text[:300]}"
        )

        # 3. Execute + poll
        r_exec = http_client.post(
            f"{API_PREFIX}/projects/{project_id}/execute",
            json={}, headers=auth_headers,
        )
        assert r_exec.status_code in (200, 201, 202), (
            f"execute non ok: {r_exec.status_code} — {r_exec.text[:300]}"
        )
        body = r_exec.json()
        task_id = body.get("task_id") or body.get("id")
        assert task_id, f"execute senza task_id: {body}"

        final = _poll_task(http_client, auth_headers, str(task_id))
        status = (final.get("status") or "").lower()
        assert status in {"success", "completed"}, (
            f"Task non success: status={status!r}, body={final}"
        )

        # 4. Verifica 12 righe
        result = final.get("result") or {}
        candidates = (
            result.get("rows"),
            result.get("row_count"),
            result.get("rows_count"),
            result.get("count"),
            final.get("rows"),
            final.get("row_count"),
        )
        rows_value = next(
            (v for v in candidates if isinstance(v, int)), None,
        )
        assert rows_value == 12, (
            f"EXT-021 atteso 12 righe, ottenuto rows_value={rows_value!r}. "
            f"Final: {final}. Result: {result}"
        )
    finally:
        http_client.delete(
            f"{API_PREFIX}/projects/{project_id}", headers=auth_headers,
        )


# ── EXT-028 — Estrai FTP file CSV ─────────────────────────────────────


@pytest.mark.requires_app
@pytest.mark.smoke
def test_ext_028_estrai_ftp_csv(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    ftp_datasource_id: str,
) -> None:
    """EXT-028 — Estrai `csv/customers_basic.csv` via FTP nel canvas.

    Catalog `ext.yaml`: dataset `ftp://tester@ftp:21/fixtures/csv/
    customers_basic.csv`, assertion `download + parse csv`. Il file
    contiene 12 customer (header escluso).

    Modulo MANIPOLA con un canvas `datasource_node(mode=source) ->
    result`. Il loader `manipola/datasource_node_loading.py`
    materializza il file remoto in un tempfile e lo parsa via
    `load_dataframe_from_file`. Visibility owned-by-group + share
    via `datasource_shares` (mirror della policy HTTP).

    Profilo `files` richiesto — `ftp_datasource_id` skipa se assente.
    """
    proj_payload = {
        "name": f"e2e-ext028-{uuid.uuid4().hex[:6]}",
        "description": "EXT-028: estrai FTP CSV",
        "project_type": "manipola",
    }
    r_proj = http_client.post(
        f"{API_PREFIX}/projects", json=proj_payload, headers=auth_headers,
    )
    assert r_proj.status_code in (200, 201), (
        f"POST project: {r_proj.status_code} — {r_proj.text[:200]}"
    )
    pj = r_proj.json()
    project_id = (pj.get("metadata") or {}).get("id") or pj.get("id")
    assert project_id, f"project response senza id: {pj}"

    try:
        ds_id = f"ds_ftp_{uuid.uuid4().hex[:6]}"
        res_id = f"res_{uuid.uuid4().hex[:6]}"
        canvas = {
            "nodes": [
                {
                    "id": ds_id,
                    "type": "datasource_node",
                    "position": {"x": 100, "y": 100},
                    "datasource_id": ftp_datasource_id,
                    "mode": "source",
                    "file_mode": "fixed",
                    "file_path": "csv/customers_basic.csv",
                    "format": "csv",
                },
                {
                    "id": res_id,
                    "type": "result",
                    "position": {"x": 400, "y": 100},
                },
            ],
            "edges": [
                {
                    "id": f"e_ds_res_{uuid.uuid4().hex[:4]}",
                    "source": ds_id,
                    "target": res_id,
                },
            ],
        }
        r_put = http_client.put(
            f"{API_PREFIX}/projects/{project_id}/canvas",
            json=canvas, headers=auth_headers,
        )
        assert r_put.status_code == 200, (
            f"PUT canvas: {r_put.status_code} — {r_put.text[:300]}"
        )

        r_exec = http_client.post(
            f"{API_PREFIX}/projects/{project_id}/execute",
            json={}, headers=auth_headers,
        )
        assert r_exec.status_code in (200, 201, 202), (
            f"execute: {r_exec.status_code} — {r_exec.text[:300]}"
        )
        body = r_exec.json()
        task_id = body.get("task_id") or body.get("id")
        assert task_id, f"execute senza task_id: {body}"

        final = _poll_task(http_client, auth_headers, str(task_id), timeout=90)
        status = (final.get("status") or "").lower()
        assert status in {"success", "completed"}, (
            f"Task non success: status={status!r}, body={final}"
        )

        result = final.get("result") or {}
        candidates = (
            result.get("rows"),
            result.get("row_count"),
            result.get("rows_count"),
            result.get("count"),
            final.get("rows"),
            final.get("row_count"),
        )
        rows_value = next(
            (v for v in candidates if isinstance(v, int)), None,
        )
        assert rows_value == 12, (
            f"EXT-028 atteso 12 righe, ottenuto rows_value={rows_value!r}. "
            f"Final: {final}. Result: {result}"
        )
    finally:
        http_client.delete(
            f"{API_PREFIX}/projects/{project_id}", headers=auth_headers,
        )
