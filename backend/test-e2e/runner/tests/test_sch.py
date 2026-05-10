"""SCH-001..SCH-XXX — Scheduler smokes.

Catalogo: backend/test-e2e/catalog/sch.yaml
"""

from __future__ import annotations

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


# ── SCH-001 — Cron schedule esegue canvas ─────────────────────────────


@pytest.mark.profile_core
@pytest.mark.requires_app
@pytest.mark.smoke
def test_sch_001_schedule_manual_run(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    postgres_conn_id: str,
) -> None:
    """SCH-001 — Pipeline ESTRAI piloata da uno schedule con run
    manuale. Catalog `sch.yaml` assertion: "run inserito in
    log_runs, status `success`".

    Non aspettiamo che il cron scatti — usiamo il manual trigger
    `POST /schedules/{id}/run` che equivale al fire del beat
    scheduler ma e' deterministico per il test. Il task Celery
    ritorna immediatamente con `task_id` da pollare; quando
    raggiunge lo stato terminale, una entry e' stata scritta nel
    catalogo `runs` (visibile via `GET /schedules/{id}/runs`).
    """
    # 1. Progetto ESTRAI customers (riusa il pattern EXT-001)
    proj = http_client.post(
        f"{API_PREFIX}/projects",
        json={
            "name": f"e2e-sch001-{uuid.uuid4().hex[:6]}",
            "description": "SCH-001 source project",
            "project_type": "estrai",
            "default_connection_id": postgres_conn_id,
        },
        headers=auth_headers,
    )
    assert proj.status_code in (200, 201), (
        f"POST project: {proj.status_code} — {proj.text[:200]}"
    )
    pj = proj.json()
    project_id = (pj.get("metadata") or {}).get("id") or pj.get("id")
    assert project_id, f"project response senza id: {pj}"

    src_id = f"src_{uuid.uuid4().hex[:6]}"
    res_id = f"res_{uuid.uuid4().hex[:6]}"
    canvas = {
        "nodes": [
            {
                "id": src_id, "type": "source",
                "position": {"x": 100, "y": 100},
                "connection_id": postgres_conn_id,
                "table_name": "customers",
                "columns": [
                    {"name": "id",        "type": "int", "visible": True},
                    {"name": "city_id",   "type": "int", "visible": True},
                    {"name": "full_name", "type": "str", "visible": True},
                    {"name": "email",     "type": "str", "visible": True},
                ],
            },
            {
                "id": res_id, "type": "result",
                "position": {"x": 400, "y": 100},
            },
        ],
        "edges": [
            {"id": f"e_{uuid.uuid4().hex[:6]}",
             "source": src_id, "target": res_id},
        ],
    }
    r_put = http_client.put(
        f"{API_PREFIX}/projects/{project_id}/canvas",
        json=canvas, headers=auth_headers,
    )
    assert r_put.status_code == 200, (
        f"PUT canvas: {r_put.status_code} — {r_put.text[:300]}"
    )

    schedule_id = None
    try:
        # 2. Crea schedule (cron infrequente: 23:59 il 31 dicembre,
        # cosi' il beat scheduler non lo scatena durante il test).
        # Il manual trigger aggira il cron e parte subito.
        r_sched = http_client.post(
            f"{API_PREFIX}/schedules",
            json={
                "project_id": project_id,
                "cron_expression": "59 23 31 12 *",
                "timezone": "Europe/Rome",
                "description": "SCH-001 schedule (manual-only)",
                "use_cache": False,
                "max_retries": 0,
                "retry_backoff_seconds": [60],
                "append_conflicts": "skip",
                "log_skipped_rows": True,
                "concurrency_max_queue": 1,
            },
            headers=auth_headers,
        )
        assert r_sched.status_code in (200, 201), (
            f"POST schedule: {r_sched.status_code} — {r_sched.text[:200]}"
        )
        schedule_id = r_sched.json().get("id") or r_sched.json().get(
            "schedule_id"
        )
        assert schedule_id, f"schedule response senza id: {r_sched.json()}"

        # 3. Manual trigger
        r_run = http_client.post(
            f"{API_PREFIX}/schedules/{schedule_id}/run",
            headers=auth_headers,
        )
        assert r_run.status_code in (200, 201, 202), (
            f"POST schedule run: {r_run.status_code} — {r_run.text[:200]}"
        )
        run_body = r_run.json()
        task_id = run_body.get("task_id")
        assert task_id, f"run senza task_id: {run_body}"

        # 4. Poll schedule_pipeline_task
        final = _poll_task(http_client, auth_headers, str(task_id), timeout=90)
        status = (final.get("status") or "").lower()
        assert status in {"success", "completed"}, (
            f"SCH-001 schedule task non success: status={status!r}, "
            f"body={final}"
        )

        # 5. Body del task pipeline ritornato dal scheduler.
        result = final.get("result") or {}
        assert result.get("success") is True, (
            f"SCH-001 task result.success != True: {result}"
        )

        # 6. Verifica runs catalog: dopo il completamento ci deve
        # essere almeno una entry. Il `ScheduleRunsCatalog.list_runs`
        # fa fallback su `schedule_logs` (event PIPELINE_COMPLETED)
        # quando il filesystem e' vuoto — necessario post refactor
        # blob→DB. Il `node_meta_run_id_fkey` race precedentemente
        # presente e' fixato da `execution_manager` chiamando
        # `intermediate.run_lifecycle.create_run` prima dello
        # `execute_pipeline`.
        deadline = time.monotonic() + 10
        runs_count = 0
        last_body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            r_runs = http_client.get(
                f"{API_PREFIX}/schedules/{schedule_id}/runs",
                headers=auth_headers,
            )
            if r_runs.status_code == 200:
                last_body = r_runs.json()
                runs_count = last_body.get("runs_count") or 0
                if runs_count >= 1:
                    break
            time.sleep(1)
        assert runs_count >= 1, (
            f"SCH-001 atteso >=1 run nel catalogo, ottenuto "
            f"runs_count={runs_count}. Body: {last_body}"
        )
    finally:
        if schedule_id:
            http_client.delete(
                f"{API_PREFIX}/schedules/{schedule_id}",
                headers=auth_headers,
            )
        http_client.delete(
            f"{API_PREFIX}/projects/{project_id}", headers=auth_headers,
        )
