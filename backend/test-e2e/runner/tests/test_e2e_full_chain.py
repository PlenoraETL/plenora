"""
E2E full-chain integration test (catch FE↔BE schema mismatches).

Motivazione (post audit cross-modulo, ~34 bug fix):
La maggioranza dei bug strutturali della sessione era dovuta a
disallineamenti tra cosa il FE serializza nel canvas e cosa il
Pydantic `Node` accetta (es. `dest_type=rest` non in Literal, campi
`rest_url`/`rest_method` non dichiarati, auth `in/name/value` vs
`location/key_name/key_value`). Erano errori "shallow" — fix di 1-5
righe — ma non emergevano dai test unitari per-modulo perche'
ogni modulo testa il proprio path; il disallineamento si manifesta
SOLO al boundary "FE canvas → executor reale".

Questo test esercita la chain plenora end-to-end:
    ESTRAI → MANIPOLA (project_source) → CARICA (DB destination)
    + Schedule manuale che ri-esegue la pipeline.

Un nuovo bug di tipo "campo FE droppato silenziosamente da Pydantic"
farebbe fallire il PUT canvas o l'execute con un errore parlante,
catturando la regressione prima di un audit manuale.

Marker:
- ``profile_core``: pg-seed + fake-rest
- ``requires_app``: backend FastAPI + worker Celery up

Run:
    docker compose up -d
    pytest -m "profile_core and requires_app" test_e2e_full_chain.py -v
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
import pytest


API_PREFIX = "/api"


def _exec_and_wait(
    http: httpx.Client,
    headers: dict[str, str],
    project_id: str,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """POST /projects/{id}/execute, polling task fino a stato terminale."""
    r = http.post(
        f"{API_PREFIX}/projects/{project_id}/execute",
        headers=headers,
        json={"force_recalculate": True},
    )
    assert r.status_code == 200, f"execute fallita: {r.status_code} {r.text[:200]}"
    task_id = r.json().get("task_id")
    assert task_id, "task_id mancante nella response execute"

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.5)
        ts = http.get(f"{API_PREFIX}/tasks/{task_id}", headers=headers)
        if ts.status_code != 200:
            continue
        body = ts.json()
        if body.get("status") in ("success", "failed"):
            return body
    pytest.fail(f"timeout su task {task_id} dopo {timeout_s}s")


def _put_canvas(
    http: httpx.Client,
    headers: dict[str, str],
    project_id: str,
    canvas: dict[str, Any],
) -> None:
    r = http.put(
        f"{API_PREFIX}/projects/{project_id}/canvas",
        headers=headers,
        json=canvas,
    )
    assert r.status_code == 200, (
        f"PUT canvas fallita: {r.status_code}\n"
        f"body request: {json.dumps(canvas)[:400]}\n"
        f"body response: {r.text[:600]}"
    )


def _delete_silent(http: httpx.Client, path: str, headers: dict[str, str]) -> None:
    try:
        http.delete(path, headers=headers)
    except Exception:
        pass


@pytest.mark.profile_core
@pytest.mark.requires_app
def test_e2e_full_chain(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """ESTRAI → MANIPOLA → CARICA (DB) → Schedule manual run.

    Tutti gli artifact creati vengono cancellati a fine test, anche
    in caso di failure intermedio (try/finally su lista cleanup).
    """
    suffix = uuid.uuid4().hex[:8]
    cleanup_projects: list[str] = []
    cleanup_schedules: list[str] = []
    cleanup_tables: list[str] = []

    try:
        # ───── ESTRAI ─────
        # Progetto + canvas con sorgente `customers` (22 righe seed).
        estrai_name = f"e2e-estrai-{suffix}"
        r = http_client.post(
            f"{API_PREFIX}/projects",
            headers=auth_headers,
            json={
                "name": estrai_name,
                "project_type": "estrai",
                "default_connection_id": postgres_conn_id,
            },
        )
        assert r.status_code == 201, f"crea ESTRAI: {r.status_code} {r.text[:200]}"
        estrai_id = r.json()["metadata"]["id"]
        cleanup_projects.append(estrai_id)

        estrai_canvas = {
            "nodes": [
                {
                    "id": "src",
                    "type": "source",
                    "position": {"x": 50, "y": 100},
                    "table_name": "customers",
                    "alias": "customers",
                    "connection_id": postgres_conn_id,
                    "columns": [
                        {"name": "id", "type": "int", "visible": True},
                        {"name": "full_name", "type": "str", "visible": True},
                        {"name": "status", "type": "str", "visible": True},
                    ],
                    "filters": [],
                },
                {
                    "id": "res",
                    "type": "resultNode",
                    "position": {"x": 400, "y": 100},
                    "format": "csv",
                    "fileName": "output",
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "res"}],
        }
        _put_canvas(http_client, auth_headers, estrai_id, estrai_canvas)
        out = _exec_and_wait(http_client, auth_headers, estrai_id)
        result = out.get("result", {})
        assert result.get("success") is True, f"ESTRAI: {result}"
        assert result.get("rows_count") == 22, (
            f"ESTRAI: attese 22 righe customers, got {result.get('rows_count')}"
        )

        # ───── MANIPOLA ─────
        # Project source che consuma il risultato ESTRAI + filter status=active.
        man_name = f"e2e-manipola-{suffix}"
        r = http_client.post(
            f"{API_PREFIX}/projects",
            headers=auth_headers,
            json={"name": man_name, "project_type": "manipola"},
        )
        assert r.status_code == 201
        man_id = r.json()["metadata"]["id"]
        cleanup_projects.append(man_id)

        man_canvas = {
            "nodes": [
                {
                    "id": "src",
                    "type": "project_source",
                    "position": {"x": 50, "y": 100},
                    "source_project_id": estrai_id,
                    "columns": [
                        {"name": "id", "type": "int", "visible": True},
                        {"name": "full_name", "type": "str", "visible": True},
                        {"name": "status", "type": "str", "visible": True},
                    ],
                },
                {
                    "id": "flt",
                    "type": "transform",
                    "position": {"x": 350, "y": 100},
                    "transform_op": "filter",
                    "config": {
                        "column": "status",
                        "operator": "==",
                        "value": "active",
                    },
                },
                {
                    "id": "res",
                    "type": "result",
                    "position": {"x": 650, "y": 100},
                    "format": "csv",
                    "fileName": "out",
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "flt"},
                {"id": "e2", "source": "flt", "target": "res"},
            ],
        }
        _put_canvas(http_client, auth_headers, man_id, man_canvas)
        out = _exec_and_wait(http_client, auth_headers, man_id)
        result = out.get("result", {})
        assert result.get("success") is True, f"MANIPOLA: {result}"
        assert (result.get("rows_count") or 0) > 0, (
            f"MANIPOLA filter active deve produrre almeno 1 riga, got {result}"
        )
        man_rows = result["rows_count"]

        # ───── CARICA (DB destination) ─────
        # Stesso MANIPOLA project ma con un nodo `carica` collegato al
        # result che scrive la tabella in PostgreSQL e2e.
        target_table = f"e2e_carica_target_{suffix}"
        cleanup_tables.append(target_table)
        carica_canvas = json.loads(json.dumps(man_canvas))  # deep copy
        carica_canvas["nodes"].append(
            {
                "id": "car",
                "type": "carica",
                "position": {"x": 950, "y": 100},
                "dest_type": "db",
                "connection_id": postgres_conn_id,
                "schema_name": "public",
                "table_name": target_table,
                "write_mode": "create",
            }
        )
        carica_canvas["edges"].append({"id": "e3", "source": "res", "target": "car"})
        _put_canvas(http_client, auth_headers, man_id, carica_canvas)
        out = _exec_and_wait(http_client, auth_headers, man_id)
        pipeline_result = out.get("result", {})
        assert pipeline_result.get("success") is True, (
            f"CARICA pipeline: {pipeline_result}"
        )
        # Il task CARICA e' sub-task: aspettiamo il suo completamento
        carica_info = pipeline_result.get("carica") or {}
        carica_tasks = carica_info.get("tasks") or []
        assert carica_tasks, f"CARICA non triggerato: {pipeline_result}"
        carica_task_id = carica_tasks[0].get("task_id")
        deadline = time.time() + 30
        carica_status = None
        while time.time() < deadline:
            time.sleep(0.5)
            ts = http_client.get(
                f"{API_PREFIX}/tasks/{carica_task_id}", headers=auth_headers
            )
            if ts.status_code == 200:
                body = ts.json()
                if body.get("status") in ("success", "failed"):
                    carica_status = body.get("result", {})
                    break
        assert carica_status is not None, "CARICA task timeout"
        assert carica_status.get("success") is True, f"CARICA result: {carica_status}"
        assert carica_status.get("rows_written") == man_rows, (
            f"CARICA rows_written {carica_status.get('rows_written')} != "
            f"MANIPOLA rows {man_rows}"
        )

        # ───── Schedule ─────
        # Schedule manuale sul MANIPOLA project (riusa la pipeline gia'
        # validata sopra). Cron fittizio (anno 2099) per evitare beat
        # auto-firing nel mezzo del test.
        r = http_client.post(
            f"{API_PREFIX}/schedules",
            headers=auth_headers,
            json={
                "project_id": man_id,
                "cron_expression": "0 0 1 1 0",  # solo domenica 1 gennaio
                "description": f"E2E full-chain test {suffix}",
                "use_cache": False,
            },
        )
        assert r.status_code == 201, f"crea schedule: {r.status_code} {r.text[:200]}"
        sched_id = r.json().get("id")
        assert sched_id, f"schedule_id mancante: {r.json()}"
        cleanup_schedules.append(sched_id)

        r = http_client.post(
            f"{API_PREFIX}/schedules/{sched_id}/run", headers=auth_headers
        )
        assert r.status_code == 200, f"trigger schedule: {r.status_code} {r.text[:200]}"

        # Polling indiretto: aspetta che lo schedule abbia almeno 1 log
        # PIPELINE_COMPLETED. Se il log endpoint c'e' lo usiamo; in
        # alternativa il check passa appena il /runs lista una run.
        deadline = time.time() + 30
        log_found = False
        while time.time() < deadline:
            time.sleep(1)
            lr = http_client.get(
                f"{API_PREFIX}/schedules/{sched_id}/logs?limit=5",
                headers=auth_headers,
            )
            if lr.status_code != 200:
                continue
            logs = lr.json().get("logs") or []
            for log in logs:
                if log.get("event") == "PIPELINE_COMPLETED":
                    log_found = True
                    break
            if log_found:
                break
        assert log_found, (
            f"Schedule {sched_id} non ha prodotto un log PIPELINE_COMPLETED "
            f"in 30s — beat o execution_manager bloccato"
        )

    finally:
        # Cleanup deterministico anche su failure
        for sid in cleanup_schedules:
            _delete_silent(http_client, f"{API_PREFIX}/schedules/{sid}", auth_headers)
        for pid in cleanup_projects:
            _delete_silent(http_client, f"{API_PREFIX}/projects/{pid}", auth_headers)
        # Tabelle CARICA: best-effort via psycopg2 se disponibile, altrimenti
        # restano (sono in e2e-postgres, ambiente effimero comunque)
        try:
            import psycopg2
            import os

            conn = psycopg2.connect(
                host=os.getenv("PLENORA_E2E_PG_HOST", "plenora-e2e-postgres"),
                port=int(os.getenv("PLENORA_E2E_PG_PORT", "5432")),
                dbname=os.getenv("PLENORA_E2E_PG_DB", "plenora_test"),
                user=os.getenv("PLENORA_E2E_PG_USER", "plenora"),
                password=os.getenv("PLENORA_E2E_PG_PWD", "plenora_test_pwd"),
            )
            with conn:
                with conn.cursor() as cur:
                    for tbl in cleanup_tables:
                        cur.execute(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE')
            conn.close()
        except Exception:
            pass
