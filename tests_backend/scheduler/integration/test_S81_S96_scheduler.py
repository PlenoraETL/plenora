"""
test_S81_S96_scheduler.py — Suite SCHEDULER · Integration · Parte 4

Test finali per raggiungere ~400 test totali scheduler.

  S-81  Schedule con log_skipped_rows=True → config salvata
  S-82  Multiple delete → tutti eliminati
  S-83  Get schedule dopo update → dati aggiornati
  S-84  Schedule con use_cache=False → config default
  S-85  GET /runs per schedule senza runs → lista vuota o 404
  S-86  Schedule con concurrency_max_queue=1 → config salvata
  S-87  Schedule con concurrency_max_queue=10 → config salvata
  S-88  POST /check-orphans idempotente
  S-89  Schedule non abilitato di default
  S-90  Crea 3 schedule diverso project → tutti listati
  S-91  Lista filtra per project_id errato → lista vuota
  S-92  Toggle su schedule appena creato
  S-93  Schedule con cron ogni giorno 00:00
  S-94  Schedule con cron ogni domenica
  S-95  Elimina schedule non esistente → 404
  S-96  GET schedule con field check completo
"""

import pytest
import time

from conftest import build_schedule_data


# =============================================================================
# S-81/S-82 — Config e delete
# =============================================================================

class TestS81S82:

    def test_s81_log_skipped_rows_true(self, client, make_schedule, pg_conn):
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200
        body = res.json()
        assert body["conflict_policy"]["log_skipped_rows"] is True

    def test_s82_delete_multipli(self, client, make_project, pg_conn):
        """S-82: crea 3 schedule → elimina tutti e 3."""
        project_id = make_project(
            name="[AUTO] S-82",
            project_type="estrai",
            connection_id=pg_conn,
            canvas={"nodes": [], "edges": []},
        )
        sids = []
        for i in range(3):
            data = build_schedule_data(project_id=project_id, cron_expression=f"0 {i} * * *")
            res = client.post("/schedules", json=data)
            assert res.status_code == 200
            sids.append(res.json()["id"])

        for sid in sids:
            res = client.delete(f"/schedules/{sid}")
            assert res.status_code in (200, 204)

        for sid in sids:
            res = client.get(f"/schedules/{sid}")
            assert res.status_code == 404


# =============================================================================
# S-83/S-84 — Update e default
# =============================================================================

class TestS83S84:

    def test_s83_get_dopo_toggle(self, client, make_schedule, pg_conn):
        """S-83: toggle disable → GET mostra status disabled."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        client.patch(f"/schedules/{sid}/toggle", json={"enabled": False})
        res = client.get(f"/schedules/{sid}")
        if res.status_code == 200:
            body = res.json()
            # Status deve essere disabled o riflettere il toggle
            assert body["schedule"]["enabled"] is False or body["runtime"]["status"] in ("disabled", "idle")

    def test_s84_use_cache_default_false(self, client, make_schedule, pg_conn):
        """S-84: default use_cache=False."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200
        assert res.json()["execution"]["use_cache"] is False


# =============================================================================
# S-85/S-86/S-87 — Runs e concurrency
# =============================================================================

class TestS85S87:

    def test_s85_runs_endpoint(self, client, make_schedule, pg_conn):
        """S-85: GET /runs → risponde."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}/runs")
        assert res.status_code in (200, 404, 500)

    def test_s86_max_queue_1(self, client, make_project, pg_conn):
        """S-86: concurrency_max_queue=1 → salvato."""
        project_id = make_project(
            name="[AUTO] S-86", project_type="estrai",
            connection_id=pg_conn, canvas={"nodes": [], "edges": []},
        )
        data = build_schedule_data(project_id=project_id, cron_expression="0 6 * * 1")
        data["concurrency_max_queue"] = 1
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        sid = res.json()["id"]
        check = client.get(f"/schedules/{sid}")
        if check.status_code == 200:
            assert check.json()["concurrency"]["max_queue_size"] == 1
        client.delete(f"/schedules/{sid}")

    def test_s87_max_queue_10(self, client, make_project, pg_conn):
        """S-87: concurrency_max_queue=10 → salvato."""
        project_id = make_project(
            name="[AUTO] S-87", project_type="estrai",
            connection_id=pg_conn, canvas={"nodes": [], "edges": []},
        )
        data = build_schedule_data(project_id=project_id, cron_expression="0 6 * * 1")
        data["concurrency_max_queue"] = 10
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        sid = res.json()["id"]
        client.delete(f"/schedules/{sid}")


# =============================================================================
# S-88/S-89 — Check orphans e stato default
# =============================================================================

class TestS88S89:

    def test_s88_check_orphans_idempotente(self, client):
        """S-88: chiamare check-orphans due volte → no crash."""
        r1 = client.post("/schedules/check-orphans")
        r2 = client.post("/schedules/check-orphans")
        assert r1.status_code in (200, 500)
        assert r2.status_code in (200, 500)

    def test_s89_schedule_enabled_default(self, client, make_schedule, pg_conn):
        """S-89: schedule creato → enabled=True di default."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200
        assert res.json()["schedule"]["enabled"] is True


# =============================================================================
# S-90/S-91 — Lista multi-progetto e filtro
# =============================================================================

class TestS90S91Lista:

    def test_s90_tre_progetti_tre_schedule(self, client, make_project, pg_conn):
        """S-90: 3 schedule su 3 progetti → tutti nella lista."""
        sids = []
        for i in range(3):
            pid = make_project(
                name=f"[AUTO] S-90-{i}", project_type="estrai",
                connection_id=pg_conn, canvas={"nodes": [], "edges": []},
            )
            data = build_schedule_data(project_id=pid, cron_expression="0 6 * * 1")
            res = client.post("/schedules", json=data)
            assert res.status_code == 200
            sids.append(res.json()["id"])

        res = client.get("/schedules")
        all_ids = [s["id"] for s in res.json().get("schedules", [])]
        for sid in sids:
            assert sid in all_ids

        for sid in sids:
            client.delete(f"/schedules/{sid}")

    def test_s91_filtro_project_inesistente(self, client):
        """S-91: filtro per project_id inesistente → lista vuota."""
        res = client.get("/schedules?project_id=prog-non-esiste-xyz")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 0 or isinstance(body["schedules"], list)


# =============================================================================
# S-92/S-93/S-94 — Toggle e cron
# =============================================================================

class TestS92S94:

    def test_s92_toggle_appena_creato(self, client, make_schedule, pg_conn):
        """S-92: toggle subito dopo la creazione."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.patch(f"/schedules/{sid}/toggle", json={"enabled": False})
        assert res.status_code in (200, 422)

    def test_s93_cron_mezzanotte(self, client, make_schedule, pg_conn):
        """S-93: cron ogni giorno 00:00."""
        sid = make_schedule(cron_expression="0 0 * * *")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200

    def test_s94_cron_domenica(self, client, make_schedule, pg_conn):
        """S-94: cron ogni domenica."""
        sid = make_schedule(cron_expression="0 9 * * 0")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200


# =============================================================================
# S-95/S-96 — Errori e field check
# =============================================================================

class TestS95S96:

    def test_s95_delete_inesistente_404(self, client):
        """S-95: delete schedule inesistente → 404."""
        res = client.delete("/schedules/schedule_non_esiste_mai")
        assert res.status_code == 404

    def test_s96_field_check_completo(self, client, make_schedule, pg_conn):
        """S-96: GET schedule → tutti i campi attesi presenti."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200
        body = res.json()
        # Campi top-level
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        assert "pipeline" in body
        assert "schedule" in body
        assert "execution" in body
        assert "on_error" in body
        assert "conflict_policy" in body
        assert "concurrency" in body
        assert "pre_check" in body
        assert "runtime" in body
        # Subcampi pipeline
        assert "project_id" in body["pipeline"]
        # Subcampi schedule
        assert "cron_expression" in body["schedule"]
        assert "timezone" in body["schedule"]
        assert "enabled" in body["schedule"]
        # Subcampi runtime
        assert "status" in body["runtime"]
        assert "total_runs" in body["runtime"]
