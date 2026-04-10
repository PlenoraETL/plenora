"""
test_S_E2E_scheduler.py — Suite SCHEDULER · E2E Cron Execution

Prerequisiti:
  - Backend FastAPI su localhost:8000
  - Celery Worker attivo
  - Celery Beat attivo (UrbyBeatScheduler)
  - Docker PostgreSQL su localhost:5432

Test coperti:
  S-E2E-01  Cron * * * * * → pipeline ESTRAI eseguita automaticamente
  S-E2E-02  Log scritto dopo esecuzione automatica
  S-E2E-03  runtime.status == completed dopo cron
  S-E2E-04  runtime.total_runs incrementato dopo cron
  S-E2E-05  Schedule disabilitato → pipeline NON eseguita
  S-E2E-06  Run manuale → pipeline ESTRAI completa con dati reali
  S-E2E-07  Runtime aggiornato dopo run manuale
"""

import pytest
import time
import uuid

pytestmark = [pytest.mark.scheduler]

# ─── Costanti ────────────────────────────────────────────────────────────────

CRON_OGNI_MINUTO = "* * * * *"
ATTESA_CRON_SEC  = 90   # attesa massima per l'esecuzione automatica del cron
POLL_INTERVAL    = 5    # polling ogni 5 secondi


# ─── Tabella di test ─────────────────────────────────────────────────────────

TABLE_DDL = "id SERIAL PRIMARY KEY, nome TEXT, valore FLOAT"

TABLE_ROWS = [
    "INSERT INTO {table} (nome, valore) VALUES ('Roma', 1000.0)",
    "INSERT INTO {table} (nome, valore) VALUES ('Milano', 2000.0)",
    "INSERT INTO {table} (nome, valore) VALUES ('Napoli', 1500.0)",
]


def _build_estrai_canvas(table_name: str) -> dict:
    return {
        "nodes": [
            {
                "id": "node-source",
                "type": "source",
                "position": {"x": 100, "y": 200},
                "table_name": table_name,
                "columns": [
                    {"name": "id",     "type": "int",  "visible": True},
                    {"name": "nome",   "type": "str",  "visible": True},
                    {"name": "valore", "type": "real", "visible": True},
                ],
                "filters": [],
            },
            {
                "id": "node-result",
                "type": "result",
                "position": {"x": 500, "y": 200},
                "fileName": "e2e_output",
                "format": "csv",
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "node-source", "target": "node-result"}
        ],
    }


def _build_schedule_data(project_id: str, cron: str = CRON_OGNI_MINUTO) -> dict:
    return {
        "project_id":            project_id,
        "cron_expression":       cron,
        "timezone":              "UTC",
        "use_cache":             False,
        "max_retries":           0,
        "retry_backoff_seconds": [60],
        "append_conflicts":      "skip",
        "log_skipped_rows":      True,
        "concurrency_max_queue": 1,
    }


def _wait_for_cron(client, schedule_id: str, timeout: int = ATTESA_CRON_SEC) -> dict:
    """Aspetta che il cron esegua lo schedule — poll ogni POLL_INTERVAL secondi."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/schedules/{schedule_id}")
        if res.status_code == 200:
            body = res.json()
            status = body.get("runtime", {}).get("status", "idle")
            total_runs = body.get("runtime", {}).get("total_runs", 0)
            if status in ("completed", "partial", "failed") or total_runs > 0:
                return body
        time.sleep(POLL_INTERVAL)
    return {}


# =============================================================================
# S-E2E-06 — Run manuale con dati reali (test più semplice, no beat)
# =============================================================================

class TestSE2E06RunManuale:

    def test_se2e_06_run_manuale_successo(self, client, pg_conn, pg_table, make_project, poll):
        """S-E2E-06: run manuale → pipeline ESTRAI completata con dati reali."""
        table = f"test_se2e_06_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-06 run manuale",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        # Crea schedule
        data = _build_schedule_data(project_id, cron="0 6 * * 1")  # non scatta automaticamente
        res = client.post("/schedules", json=data)
        assert res.status_code == 200, f"Errore creazione schedule: {res.text}"
        schedule_id = res.json()["id"]

        try:
            # Run manuale
            res_run = client.post(f"/schedules/{schedule_id}/run")
            assert res_run.status_code == 200, f"Errore run manuale: {res_run.text}"

            body = res_run.json()

            # Se asincrono → poll
            if "task_id" in body:
                result = poll(body["task_id"])
                assert result.get("success") is True, f"Run manuale fallito: {result}"
            else:
                # Sincrono o queued
                assert body.get("success") is True or body.get("status") in ("completed", "queued")

        finally:
            client.delete(f"/schedules/{schedule_id}")

    def test_se2e_07_runtime_dopo_run_manuale(self, client, pg_conn, pg_table, make_project, poll):
        """S-E2E-07: dopo run manuale → runtime aggiornato."""
        table = f"test_se2e_07_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-07 runtime",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        data = _build_schedule_data(project_id, cron="0 6 * * 1")
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        schedule_id = res.json()["id"]

        try:
            # Run manuale
            client.post(f"/schedules/{schedule_id}/run")
            time.sleep(3)  # attendi che il task completi

            # Verifica runtime aggiornato
            res_get = client.get(f"/schedules/{schedule_id}")
            assert res_get.status_code == 200
            body = res_get.json()
            runtime = body.get("runtime", {})

            # Total runs deve essere >= 1 o status != idle
            total_runs = runtime.get("total_runs", 0)
            status = runtime.get("status", "idle")
            assert total_runs >= 1 or status != "idle", (
                f"Runtime non aggiornato dopo run manuale: {runtime}"
            )

        finally:
            client.delete(f"/schedules/{schedule_id}")


# =============================================================================
# S-E2E-01/02/03/04 — Cron automatico (richiede beat attivo)
# =============================================================================

class TestSE2E01CronAutomatico:

    def test_se2e_01_cron_eseguito(self, client, pg_conn, pg_table, make_project):
        """
        S-E2E-01: schedule con cron * * * * * → beat esegue la pipeline entro 90s.

        Richiede Celery Beat (UrbyBeatScheduler) attivo.
        Se il beat non è attivo, il test viene skippato.
        """
        # Verifica che beat sia attivo
        hc = client.get("/schedules/healthcheck")
        if hc.status_code != 200 or hc.json().get("status") != "ok":
            pytest.skip("Celery Beat non attivo — skip test cron automatico")

        table = f"test_se2e_01_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-01 cron auto",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        data = _build_schedule_data(project_id, cron=CRON_OGNI_MINUTO)
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        schedule_id = res.json()["id"]

        try:
            # Attendi l'esecuzione automatica del cron
            body = _wait_for_cron(client, schedule_id, timeout=ATTESA_CRON_SEC)
            runtime = body.get("runtime", {})

            assert runtime.get("total_runs", 0) >= 1, (
                f"Cron non eseguito entro {ATTESA_CRON_SEC}s. Runtime: {runtime}"
            )
        finally:
            client.delete(f"/schedules/{schedule_id}")

    def test_se2e_02_log_scritto_dopo_cron(self, client, pg_conn, pg_table, make_project):
        """S-E2E-02: dopo esecuzione cron → log presente."""
        hc = client.get("/schedules/healthcheck")
        if hc.status_code != 200 or hc.json().get("status") != "ok":
            pytest.skip("Celery Beat non attivo")

        table = f"test_se2e_02_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-02 log cron",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        data = _build_schedule_data(project_id)
        res = client.post("/schedules", json=data)
        schedule_id = res.json()["id"]

        try:
            _wait_for_cron(client, schedule_id)

            # Verifica log
            res_logs = client.get(f"/schedules/{schedule_id}/logs")
            assert res_logs.status_code == 200
            logs = res_logs.json()
            assert logs.get("total", 0) >= 1, (
                f"Nessun log trovato dopo esecuzione cron. Logs: {logs}"
            )
        finally:
            client.delete(f"/schedules/{schedule_id}")

    def test_se2e_03_status_completed_dopo_cron(self, client, pg_conn, pg_table, make_project):
        """S-E2E-03: dopo esecuzione cron → runtime.status == completed."""
        hc = client.get("/schedules/healthcheck")
        if hc.status_code != 200 or hc.json().get("status") != "ok":
            pytest.skip("Celery Beat non attivo")

        table = f"test_se2e_03_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-03 status cron",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        data = _build_schedule_data(project_id)
        res = client.post("/schedules", json=data)
        schedule_id = res.json()["id"]

        try:
            body = _wait_for_cron(client, schedule_id)
            status = body.get("runtime", {}).get("status", "idle")
            assert status in ("completed", "partial"), (
                f"Status atteso completed/partial, trovato: {status}"
            )
        finally:
            client.delete(f"/schedules/{schedule_id}")

    def test_se2e_04_total_runs_incrementato(self, client, pg_conn, pg_table, make_project):
        """S-E2E-04: dopo cron → runtime.total_runs >= 1."""
        hc = client.get("/schedules/healthcheck")
        if hc.status_code != 200 or hc.json().get("status") != "ok":
            pytest.skip("Celery Beat non attivo")

        table = f"test_se2e_04_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-04 total_runs",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        data = _build_schedule_data(project_id)
        res = client.post("/schedules", json=data)
        schedule_id = res.json()["id"]

        try:
            body = _wait_for_cron(client, schedule_id)
            total_runs = body.get("runtime", {}).get("total_runs", 0)
            assert total_runs >= 1, f"total_runs atteso >= 1, trovato: {total_runs}"
        finally:
            client.delete(f"/schedules/{schedule_id}")


# =============================================================================
# S-E2E-05 — Schedule disabilitato non eseguito
# =============================================================================

class TestSE2E05Disabilitato:

    def test_se2e_05_schedule_disabilitato_non_eseguito(self, client, pg_conn, pg_table, make_project):
        """S-E2E-05: schedule disabilitato → pipeline NON eseguita dal cron."""
        hc = client.get("/schedules/healthcheck")
        if hc.status_code != 200 or hc.json().get("status") != "ok":
            pytest.skip("Celery Beat non attivo")

        table = f"test_se2e_05_{uuid.uuid4().hex[:8]}"
        rows = [r.format(table=table) for r in TABLE_ROWS]
        pg_table(ddl=TABLE_DDL, rows=rows, table_name=table)

        canvas = _build_estrai_canvas(table)
        project_id = make_project(
            name=f"[AUTO] S-E2E-05 disabilitato",
            project_type="estrai",
            connection_id=pg_conn,
            canvas=canvas,
        )

        data = _build_schedule_data(project_id)
        res = client.post("/schedules", json=data)
        schedule_id = res.json()["id"]

        try:
            # Disabilita immediatamente
            client.patch(f"/schedules/{schedule_id}/toggle", json={"enabled": False})

            # Aspetta 70 secondi — il cron NON deve scattare
            time.sleep(70)

            res_get = client.get(f"/schedules/{schedule_id}")
            body = res_get.json()
            total_runs = body.get("runtime", {}).get("total_runs", 0)

            assert total_runs == 0, (
                f"Schedule disabilitato non avrebbe dovuto eseguire, "
                f"trovato total_runs={total_runs}"
            )
        finally:
            client.delete(f"/schedules/{schedule_id}")
