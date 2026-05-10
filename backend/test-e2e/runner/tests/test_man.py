"""MAN-001..MAN-XXX — Manipola smokes.

Catalogo: backend/test-e2e/catalog/man.yaml
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
    """Polla `GET /tasks/{id}` fino a stato terminale."""
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


def _create_estrai_source(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    *,
    conn_id: str,
    table: str,
    columns: list[dict[str, Any]],
) -> str:
    """Helper: crea un progetto ESTRAI con 1 source e lo esegue.

    Ritorna il `project_id` del progetto eseguito, pronto per essere
    referenziato come `project_source` da un progetto MANIPOLA
    downstream. Le `columns` accettano `alias` per rinominare i campi
    del SELECT (`SELECT id AS city_id ...`); il blob result avrà le
    colonne con il nome alias, cosi' downstream possono fare join su
    chiavi armonizzate.
    """
    proj = http_client.post(
        f"{API_PREFIX}/projects",
        json={
            "name": f"e2e-man002-src-{table}-{uuid.uuid4().hex[:6]}",
            "description": f"MAN-002 source {table}",
            "project_type": "estrai",
            "default_connection_id": conn_id,
        },
        headers=auth_headers,
    )
    assert proj.status_code in (200, 201), (
        f"POST estrai source {table}: {proj.status_code} — {proj.text[:200]}"
    )
    pj = proj.json()
    pid = (pj.get("metadata") or {}).get("id") or pj.get("id")
    assert pid, f"estrai source response senza id: {pj}"

    src_id = f"src_{uuid.uuid4().hex[:6]}"
    res_id = f"res_{uuid.uuid4().hex[:6]}"
    canvas = {
        "nodes": [
            {
                "id": src_id,
                "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": conn_id,
                "table_name": table,
                "columns": [
                    {**c, "visible": True} for c in columns
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
        f"{API_PREFIX}/projects/{pid}/canvas",
        json=canvas, headers=auth_headers,
    )
    assert r_put.status_code == 200, (
        f"PUT canvas estrai source {table}: {r_put.status_code} — "
        f"{r_put.text[:300]}"
    )
    r_exec = http_client.post(
        f"{API_PREFIX}/projects/{pid}/execute",
        json={}, headers=auth_headers,
    )
    assert r_exec.status_code in (200, 201, 202), (
        f"execute estrai source {table}: {r_exec.status_code} — "
        f"{r_exec.text[:300]}"
    )
    task_id = r_exec.json().get("task_id") or r_exec.json().get("id")
    assert task_id, f"execute estrai senza task_id: {r_exec.json()}"
    final = _poll_task(http_client, auth_headers, str(task_id), timeout=60)
    status = (final.get("status") or "").lower()
    assert status in {"success", "completed"}, (
        f"Estrai source {table} non success: {final}"
    )
    return pid


# ── MAN-002 — Inner join customers/cities/regions (3 tabelle) ─────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_man_002_inner_join_three_tables(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """MAN-002 — Pipeline MANIPOLA con 3 project_source + 2
    multi_join inner concatenati.

    `manipola/executor._esegui_multi_join` richiede che tutte le
    `join_keys` esistano in **tutti** gli input → un singolo
    multi_join non puo' join-are 3 tabelle con chiavi diverse.
    Soluzione canonica: due multi_join in cascata, uno per chiave.

    Topologia canvas:
        ps_customers ┐
                     mj1(city_id) ┐
        ps_cities ───┘             mj2(region_id) → result
        ps_regions ────────────────┘

    Chiavi armonizzate via alias SQL nei progetti source ESTRAI:
      customers.city_id     (FK gia' chiamata cosi')
      cities.id   AS city_id
      cities.region_id      (FK gia' chiamata cosi')
      regions.id  AS region_id

    Atteso: il numero di righe == il `COUNT(*)` di un INNER JOIN
    SQL puro su quelle 3 tabelle nel seed `plenora-e2e-postgres`.
    Calcolato a runtime via psycopg2 per evitare hardcode dipendente
    dal seed.
    """
    # 0. Calcola il valore atteso via SQL diretto sul seed (psycopg2
    # gia' nel runner image; stesse env vars di postgres_conn_id).
    import psycopg2
    pg_kwargs = dict(
        host=os.getenv("PLENORA_E2E_PG_HOST", "plenora-e2e-postgres"),
        port=int(os.getenv("PLENORA_E2E_PG_PORT", "5432")),
        user=os.getenv("PLENORA_E2E_PG_USER", "plenora"),
        password=os.getenv("PLENORA_E2E_PG_PWD", "plenora_test_pwd"),
        dbname=os.getenv("PLENORA_E2E_PG_DB", "plenora_test"),
    )
    conn = psycopg2.connect(**pg_kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM customers c
                INNER JOIN cities ci ON c.city_id = ci.id
                INNER JOIN regions r ON ci.region_id = r.id
            """)
            expected_rows = int(cur.fetchone()[0])
    finally:
        conn.close()
    assert expected_rows > 0, (
        f"MAN-002: SQL ground-truth ritorna 0 righe — il seed "
        f"`plenora-e2e-postgres` potrebbe non contenere i join"
    )

    # 1. Crea 3 progetti ESTRAI source. Aliasing colonne PK -> FK
    # per uniformare le chiavi del downstream multi_join.
    pid_customers = _create_estrai_source(
        http_client, auth_headers, conn_id=postgres_conn_id,
        table="customers", columns=[
            {"name": "id",       "type": "int"},
            {"name": "city_id",  "type": "int"},
            {"name": "full_name","type": "str"},
            {"name": "email",    "type": "str"},
        ],
    )
    pid_cities = _create_estrai_source(
        http_client, auth_headers, conn_id=postgres_conn_id,
        table="cities", columns=[
            {"name": "id",        "alias": "city_id",   "type": "int"},
            {"name": "name",      "alias": "city_name", "type": "str"},
            {"name": "region_id", "type": "int"},
        ],
    )
    pid_regions = _create_estrai_source(
        http_client, auth_headers, conn_id=postgres_conn_id,
        table="regions", columns=[
            {"name": "id",   "alias": "region_id",   "type": "int"},
            {"name": "name", "alias": "region_name", "type": "str"},
        ],
    )

    cleanup_pids = [pid_customers, pid_cities, pid_regions]

    # 2. Crea progetto MANIPOLA col canvas multi-join.
    proj = http_client.post(
        f"{API_PREFIX}/projects",
        json={
            "name": f"e2e-man002-{uuid.uuid4().hex[:6]}",
            "description": "MAN-002: inner join 3 tabelle",
            "project_type": "manipola",
        },
        headers=auth_headers,
    )
    assert proj.status_code in (200, 201), (
        f"POST manipola: {proj.status_code} — {proj.text[:200]}"
    )
    pj = proj.json()
    project_id = (pj.get("metadata") or {}).get("id") or pj.get("id")
    assert project_id, f"manipola response senza id: {pj}"
    cleanup_pids.append(project_id)

    try:
        ps_customers = f"ps_cust_{uuid.uuid4().hex[:6]}"
        ps_cities    = f"ps_cit_{uuid.uuid4().hex[:6]}"
        ps_regions   = f"ps_reg_{uuid.uuid4().hex[:6]}"
        mj1 = f"mj1_{uuid.uuid4().hex[:6]}"
        mj2 = f"mj2_{uuid.uuid4().hex[:6]}"
        res = f"res_{uuid.uuid4().hex[:6]}"

        # I project_source hanno `columns` non vuoti per consentire
        # al filter_visible_columns di passthrough tutto. Lasciamo
        # `[]` cosi' il loader prende tutte le colonne del blob.
        canvas = {
            "nodes": [
                {"id": ps_customers, "type": "project_source",
                 "position": {"x": 100, "y":  50},
                 "source_project_id": pid_customers, "columns": []},
                {"id": ps_cities, "type": "project_source",
                 "position": {"x": 100, "y": 200},
                 "source_project_id": pid_cities, "columns": []},
                {"id": ps_regions, "type": "project_source",
                 "position": {"x": 100, "y": 350},
                 "source_project_id": pid_regions, "columns": []},
                {"id": mj1, "type": "multi_join",
                 "position": {"x": 400, "y": 125},
                 "join_keys": ["city_id"], "multi_join_type": "inner"},
                {"id": mj2, "type": "multi_join",
                 "position": {"x": 700, "y": 200},
                 "join_keys": ["region_id"], "multi_join_type": "inner"},
                {"id": res, "type": "result",
                 "position": {"x": 1000, "y": 200}},
            ],
            "edges": [
                {"id": f"e1_{uuid.uuid4().hex[:6]}",
                 "source": ps_customers, "target": mj1, "join_type": "inner"},
                {"id": f"e2_{uuid.uuid4().hex[:6]}",
                 "source": ps_cities,    "target": mj1, "join_type": "inner"},
                {"id": f"e3_{uuid.uuid4().hex[:6]}",
                 "source": mj1,          "target": mj2, "join_type": "inner"},
                {"id": f"e4_{uuid.uuid4().hex[:6]}",
                 "source": ps_regions,   "target": mj2, "join_type": "inner"},
                {"id": f"e5_{uuid.uuid4().hex[:6]}",
                 "source": mj2,          "target": res, "join_type": "output"},
            ],
        }
        r_put = http_client.put(
            f"{API_PREFIX}/projects/{project_id}/canvas",
            json=canvas, headers=auth_headers,
        )
        assert r_put.status_code == 200, (
            f"PUT canvas manipola: {r_put.status_code} — "
            f"{r_put.text[:300]}"
        )

        # 3. Execute + poll
        r_exec = http_client.post(
            f"{API_PREFIX}/projects/{project_id}/execute",
            json={}, headers=auth_headers,
        )
        assert r_exec.status_code in (200, 201, 202), (
            f"execute manipola: {r_exec.status_code} — {r_exec.text[:300]}"
        )
        task_id = r_exec.json().get("task_id") or r_exec.json().get("id")
        assert task_id, f"execute manipola senza task_id"

        final = _poll_task(http_client, auth_headers, str(task_id), timeout=120)
        status = (final.get("status") or "").lower()
        assert status in {"success", "completed"}, (
            f"Task manipola non success: status={status!r}, body={final}"
        )

        # 4. Verifica conteggio
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
        assert rows_value == expected_rows, (
            f"MAN-002 atteso {expected_rows} righe (SQL ground-truth), "
            f"ottenuto rows_value={rows_value!r}. Final: {final}"
        )
    finally:
        for pid in cleanup_pids:
            http_client.delete(
                f"{API_PREFIX}/projects/{pid}", headers=auth_headers,
            )
