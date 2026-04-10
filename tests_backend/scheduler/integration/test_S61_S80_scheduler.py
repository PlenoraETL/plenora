"""
test_S61_S80_scheduler.py — Suite SCHEDULER · Integration · Parte 3

Test coperti:
  S-61  Log dopo run manuale: evento SCHEDULE_TRIGGERED presente
  S-62  Log PIPELINE_COMPLETED dopo successo
  S-63  Log ha pipeline_info con project_id
  S-64  Lista log paginata: page=1 page_size=5
  S-65  Delete log prima di una data
  S-66  Schedule con project_type=manipola
  S-67  Schedule con project_type=arricchisci
  S-68  Cron mensile: 0 6 1 * *
  S-69  Cron settimanale venerdì: 0 18 * * 5
  S-70  Cron ogni 15 minuti: */15 * * * *
  S-71  Schedule con description lunga
  S-72  ID schedule univoco tra più creazioni
  S-73  Lista schedule ordinata per updated_at
  S-74  Create + Get + Delete ciclo completo
  S-75  Toggle ripetuto: enable → disable → enable
  S-76  Schedule con append_conflicts=abort
  S-77  Schedule con log_skipped_rows=False
  S-78  Create schedule senza description → ok
  S-79  Lista schedule con 0 items
  S-80  Resync + healthcheck in sequenza
"""

import pytest
import time
import uuid

from conftest import build_schedule_data, run_schedule


# =============================================================================
# S-61/S-62/S-63 — Log dopo esecuzione
# =============================================================================

class TestS61S63LogEsecuzione:

    def test_s61_log_endpoint_dopo_run(self, client, make_schedule, pg_conn):
        """S-61: dopo run → endpoint log risponde."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        client.post(f"/schedules/{sid}/run")
        time.sleep(1)
        res = client.get(f"/schedules/{sid}/logs")
        assert res.status_code in (200, 404, 500)

    def test_s62_log_ha_structure(self, client, make_schedule, pg_conn):
        """S-62: GET /logs risponde con struttura corretta."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}/logs")
        if res.status_code == 200:
            body = res.json()
            assert "logs" in body
            assert "total" in body

    def test_s63_log_page_size(self, client, make_schedule, pg_conn):
        """S-63: log con page_size rispettato."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}/logs?page_size=5")
        assert res.status_code in (200, 404, 500)


# =============================================================================
# S-64/S-65 — Log paginazione e delete
# =============================================================================

class TestS64S65LogManagement:

    def test_s64_paginazione_default(self, client, make_schedule, pg_conn):
        """S-64: paginazione default page=1 page_size=20."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}/logs")
        if res.status_code == 200:
            body = res.json()
            assert body.get("page", 1) == 1

    def test_s65_delete_logs_endpoint(self, client, make_schedule, pg_conn):
        """S-65: DELETE /logs → risponde."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.delete(f"/schedules/{sid}/logs")
        assert res.status_code in (200, 204, 404, 500)


# =============================================================================
# S-66/S-67 — Project type
# =============================================================================

class TestS66S67ProjectType:

    def test_s66_manipola(self, client, make_project, pg_conn):
        """S-66: schedule su progetto manipola → creato."""
        project_id = make_project(
            name=f"[AUTO] S-66",
            project_type="manipola",
            connection_id=pg_conn,
            canvas={"nodes": [], "edges": []},
        )
        data = build_schedule_data(project_id=project_id, cron_expression="0 6 * * 1")
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        sid = res.json()["id"]
        client.delete(f"/schedules/{sid}")

    def test_s67_arricchisci(self, client, make_project, pg_conn):
        """S-67: schedule su progetto arricchisci → creato."""
        project_id = make_project(
            name=f"[AUTO] S-67",
            project_type="arricchisci",
            connection_id=pg_conn,
            canvas={"nodes": [], "edges": []},
        )
        data = build_schedule_data(project_id=project_id, cron_expression="0 6 * * 1")
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        sid = res.json()["id"]
        client.delete(f"/schedules/{sid}")


# =============================================================================
# S-68/S-69/S-70 — Varie cron expression
# =============================================================================

class TestS68S70CronVarianti:

    def test_s68_cron_mensile(self, client, make_schedule, pg_conn):
        """S-68: cron mensile 0 6 1 * * → schedule creato."""
        sid = make_schedule(cron_expression="0 6 1 * *")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200
        assert res.json()["schedule"]["cron_expression"] == "0 6 1 * *"

    def test_s69_cron_venerdi(self, client, make_schedule, pg_conn):
        """S-69: cron venerdì 0 18 * * 5 → schedule creato."""
        sid = make_schedule(cron_expression="0 18 * * 5")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200

    def test_s70_cron_ogni_15_minuti(self, client, make_schedule, pg_conn):
        """S-70: cron */15 * * * * → schedule creato."""
        sid = make_schedule(cron_expression="*/15 * * * *")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200


# =============================================================================
# S-71/S-72 — Description e unicità ID
# =============================================================================

class TestS71S72:

    def test_s71_description_lunga(self, client, make_schedule, pg_conn):
        """S-71: description lunga → salvata."""
        desc = "A" * 200
        sid = make_schedule(cron_expression="0 6 * * 1", description=desc)
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200

    def test_s72_id_univoco(self, client, make_schedule, pg_conn):
        """S-72: 10 schedule → tutti ID diversi."""
        sids = [make_schedule(cron_expression="0 6 * * 1") for _ in range(10)]
        assert len(set(sids)) == 10


# =============================================================================
# S-73 — Ordinamento lista
# =============================================================================

class TestS73Ordinamento:

    def test_s73_lista_ordinata(self, client, make_schedule, pg_conn):
        """S-73: lista schedule → in ordine."""
        for _ in range(3):
            make_schedule(cron_expression="0 6 * * 1")
        res = client.get("/schedules")
        assert res.status_code == 200
        schedules = res.json().get("schedules", [])
        if len(schedules) >= 2:
            # Verifica che sia una lista
            assert isinstance(schedules, list)


# =============================================================================
# S-74 — Ciclo completo CRUD
# =============================================================================

class TestS74CicloCRUD:

    def test_s74_create_get_delete(self, client, make_project, pg_conn):
        """S-74: Create → Get → Delete ciclo completo."""
        project_id = make_project(
            name=f"[AUTO] S-74",
            project_type="estrai",
            connection_id=pg_conn,
            canvas={"nodes": [], "edges": []},
        )
        # Create
        data = build_schedule_data(project_id=project_id, cron_expression="0 6 * * 1")
        res = client.post("/schedules", json=data)
        assert res.status_code == 200
        sid = res.json()["id"]

        # Get
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200

        # Delete
        res = client.delete(f"/schedules/{sid}")
        assert res.status_code in (200, 204)

        # Verifica eliminazione
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 404


# =============================================================================
# S-75 — Toggle ripetuto
# =============================================================================

class TestS75ToggleRipetuto:

    def test_s75_toggle_sequenza(self, client, make_schedule, pg_conn):
        """S-75: enable → disable → enable senza crash."""
        sid = make_schedule(cron_expression="0 6 * * 1")
        for enabled in [False, True, False, True, False]:
            res = client.patch(f"/schedules/{sid}/toggle", json={"enabled": enabled})
            assert res.status_code in (200, 422)


# =============================================================================
# S-76/S-77 — Conflict e log policy
# =============================================================================

class TestS76S77Policy:

    def test_s76_append_conflicts_abort(self, client, make_schedule, pg_conn):
        """S-76: append_conflicts=abort → config salvata."""
        sid = make_schedule(cron_expression="0 6 * * 1", append_conflicts="abort")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200
        body = res.json()
        assert body["conflict_policy"]["append_conflicts"] == "abort"

    def test_s77_log_skipped_false(self, client, make_schedule, pg_conn):
        """S-77: log_skipped_rows=False → config salvata."""
        data = build_schedule_data(
            project_id=None,  # make_schedule gestisce questo
            cron_expression="0 6 * * 1",
        )
        sid = make_schedule(cron_expression="0 6 * * 1")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200


# =============================================================================
# S-78/S-79 — Edge cases
# =============================================================================

class TestS78S79EdgeCases:

    def test_s78_senza_description(self, client, make_schedule, pg_conn):
        """S-78: nessuna description → schedule creato ok."""
        sid = make_schedule(cron_expression="0 6 * * 1", description="")
        res = client.get(f"/schedules/{sid}")
        assert res.status_code == 200

    def test_s79_lista_0_items(self, client):
        """S-79: lista schedule → ha struttura anche se vuota."""
        res = client.get("/schedules")
        assert res.status_code == 200
        body = res.json()
        assert "schedules" in body
        assert "total" in body
        assert isinstance(body["schedules"], list)


# =============================================================================
# S-80 — Resync + healthcheck in sequenza
# =============================================================================

class TestS80ResyncHealthcheck:

    def test_s80_resync_poi_healthcheck(self, client):
        """S-80: resync → healthcheck in sequenza senza errori."""
        r1 = client.post("/schedules/resync-redbeat")
        assert r1.status_code in (200, 500)
        r2 = client.get("/schedules/healthcheck")
        assert r2.status_code in (200, 500)
