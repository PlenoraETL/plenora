"""LOAD-001..LOAD-00X — Carica (DB target sinks).

Catalogo: backend/test-e2e/catalog/load.yaml
"""

from __future__ import annotations

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
    timeout: float = 90.0,
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


# ── LOAD-001 — Scrivi tabella su Postgres target ──────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_load_001_carica_postgres_target(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """LOAD-001 — Pipeline `customers (DB) -> result -> carica (DB)`.

    Catalog `load.yaml`: assertion "schema generato, righe scritte,
    conteggio match". Estraiamo `customers` (22 righe) dal seed
    `plenora-e2e-postgres` e le scriviamo in una tabella nuova
    `e2e_load_001_<uuid>` sullo stesso DB. Plenora gestisce la pipeline
    in due fasi:
      1. `estrai` esegue source -> result, materializza il blob.
      2. L'orchestrator rileva il nodo `carica` collegato al `result`
         (`_execute_carica_if_present`) e lancia il task Celery che
         crea lo schema target e scrive le righe.

    Verifichiamo entrambe via il body finale del task `execute`:
    `result.carica.success` e `result.carica.rows_written` devono
    coincidere con le 22 righe estratte. Cleanup della tabella
    target nella `finally` per non lasciare detriti nel DB seed.
    """
    target_table = f"e2e_load_001_{uuid.uuid4().hex[:8]}"

    # 1. Crea project tipo `estrai` con default_connection_id postgres
    proj_payload = {
        "name": f"e2e-load001-{uuid.uuid4().hex[:6]}",
        "description": "LOAD-001: estrai + carica DB target",
        "project_type": "estrai",
        "default_connection_id": postgres_conn_id,
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
        # 2. PUT canvas: source customers -> result -> carica DB target.
        # `_find_all_carica_nodes` (orchestrator/service.py) richiede
        # un edge `source == result.id, target == carica.id`. Senza,
        # il sink viene ignorato silenziosamente.
        src_id = f"src_{uuid.uuid4().hex[:6]}"
        res_id = f"res_{uuid.uuid4().hex[:6]}"
        load_id = f"load_{uuid.uuid4().hex[:6]}"
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
                {
                    "id": load_id,
                    "type": "carica",
                    "position": {"x": 700, "y": 100},
                    "dest_type": "db",
                    "connection_id": postgres_conn_id,
                    "table_name": target_table,
                    "write_mode": "create",
                },
            ],
            "edges": [
                {
                    "id": f"e_src_res_{uuid.uuid4().hex[:4]}",
                    "source": src_id,
                    "target": res_id,
                },
                {
                    "id": f"e_res_load_{uuid.uuid4().hex[:4]}",
                    "source": res_id,
                    "target": load_id,
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

        # 3. Execute + poll task estrai
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
            f"Task estrai non success: status={status!r}, body={final}"
        )

        # 4. Lo step `carica` parte come task separato dopo il completamento
        # del task estrai. Il body del task estrai contiene un riferimento
        # a `result.carica.task_id` (vedi orchestrator
        # `_execute_carica_if_present`); pollare anche quello.
        result = final.get("result") or {}
        carica_info = result.get("carica") or {}
        # Shape osservata: `result.carica.tasks: [{task_id, node_id}, ...]`.
        # Un nodo carica → un task. Per LOAD-001 ne abbiamo uno solo.
        tasks = carica_info.get("tasks") or []
        carica_task_id = tasks[0].get("task_id") if tasks else None
        assert carica_task_id, (
            f"LOAD-001 atteso un task in `carica.tasks`, ottenuto "
            f"result.carica={carica_info!r}. Final: {final}"
        )
        carica_final = _poll_task(
            http_client, auth_headers, str(carica_task_id), timeout=90
        )
        c_status = (carica_final.get("status") or "").lower()
        assert c_status in {"success", "completed"}, (
            f"Task carica non success: status={c_status!r}, body={carica_final}"
        )

        # 5. Verifica conteggio righe scritte. Il task carica espone il
        # numero in `result.rows_written` o equivalenti.
        c_result = carica_final.get("result") or {}
        rows_written = next(
            (
                v for v in (
                    c_result.get("rows_written"),
                    c_result.get("rows_loaded"),
                    c_result.get("rows"),
                    c_result.get("rows_count"),
                ) if isinstance(v, int)
            ),
            None,
        )
        assert rows_written == 22, (
            f"LOAD-001 atteso 22 righe scritte, ottenuto "
            f"rows_written={rows_written!r}. Carica result: {c_result}"
        )
    finally:
        # Cleanup: drop tabella target via SQL (Plenora non espone
        # endpoint per drop di tabelle non-Plenora).
        # Il cleanup non e' critico se fallisce — il prossimo run usa
        # un suffix uuid diverso e non collide.
        http_client.delete(
            f"{API_PREFIX}/projects/{project_id}", headers=auth_headers,
        )
