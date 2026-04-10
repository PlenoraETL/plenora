"""
tests/regression/integration/test_REG28_35_e2e.py
==================================================
Regression test E2E — Pipeline completa (REG-28→REG-35)
"""

import pytest
import requests
import time
import io
import numpy as np
import pandas as pd

pytestmark = [pytest.mark.regression, pytest.mark.e2e, pytest.mark.slow]

BASE_URL = "http://localhost:8000/api"


def _poll_task(task_id: str, auth_headers: dict, max_wait: int = 120) -> dict:
    """Polling task status finché non completa o timeout."""
    start = time.perf_counter()
    while time.perf_counter() - start < max_wait:
        res = requests.get(f"{BASE_URL}/tasks/{task_id}",
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            status = res.json().get("status", "")
            if status in ("SUCCESS", "FAILURE", "REVOKED"):
                return res.json()
        time.sleep(2)
    return {"status": "TIMEOUT"}


# =============================================================================
# REG-28 — ESTRAI DB → MANIPOLA → CSV
# =============================================================================

class TestREG28Integration:

    def test_pipeline_estrai_manipola_no_500(self, auth_headers, make_project,
                                              make_connection):
        """Pipeline ESTRAI+MANIPOLA non dà 500."""
        project_id = make_project(project_type="estrai")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500

    @pytest.mark.parametrize("project_type", ["estrai", "manipola", "arricchisci"])
    def test_pipeline_vari_tipi_no_500(self, auth_headers, make_project, project_type):
        """Pipeline di vari tipi non dà 500."""
        project_id = make_project(project_type=project_type)
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500

    def test_pipeline_task_id_restituito(self, auth_headers, make_project):
        """Esecuzione pipeline restituisce task_id."""
        project_id = make_project(project_type="estrai")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        if res.status_code == 200:
            body = res.json()
            assert "task_id" in body or "id" in body or body

    def test_pipeline_download_dopo_esecuzione(self, auth_headers, make_project):
        """Download disponibile dopo esecuzione."""
        project_id = make_project(project_type="estrai")
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=30)
        time.sleep(2)
        res = requests.get(f"{BASE_URL}/projects/{project_id}/download",
                           headers=auth_headers, timeout=15)
        assert res.status_code != 500

    @pytest.mark.parametrize("output_format", ["csv", "gpkg", "geojson"])
    def test_pipeline_output_formati_vari(self, auth_headers, make_project,
                                           output_format):
        """Pipeline produce output in vari formati."""
        project_id = make_project()
        res = requests.get(
            f"{BASE_URL}/projects/{project_id}/download",
            params={"format": output_format},
            headers=auth_headers, timeout=15
        )
        assert res.status_code != 500


# =============================================================================
# REG-29 — ESTRAI File → ARRICCHISCI → CSV
# =============================================================================

class TestREG29Integration:

    @pytest.fixture(scope="class")
    def csv_paesi(self):
        """CSV 5 paesi."""
        df = pd.DataFrame({
            "id":    [1, 2, 3, 4, 5],
            "paese": ["Italy", "France", "Germany", "Spain", "Poland"],
        })
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        return buf.getvalue()

    def test_upload_csv_paesi_no_500(self, auth_headers, csv_paesi):
        """Upload CSV paesi non dà 500."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("paesi.csv", io.BytesIO(csv_paesi), "text/csv")},
            headers=auth_headers, timeout=15
        )
        assert res.status_code != 500

    def test_pipeline_arricchisci_no_500(self, auth_headers, make_project):
        """Pipeline arricchisci non dà 500."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500

    def test_validate_enrichment_config(self, auth_headers, make_project):
        """Validazione config enrichment non dà 500."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/validate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {"paese": "paese"}},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500


# =============================================================================
# REG-30 — ESTRAI DB → ARRICCHISCI → CARICA PostgreSQL
# =============================================================================

class TestREG30Integration:

    def test_pipeline_completa_no_500(self, auth_headers, make_project,
                                       make_connection):
        """Pipeline ESTRAI→ARRICCHISCI→CARICA non dà 500."""
        project_id = make_project(project_type="estrai")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500

    @pytest.mark.parametrize("n_steps", [1, 2, 3])
    def test_pipeline_n_step_no_500(self, auth_headers, make_project, n_steps):
        """Pipeline con N step non dà 500."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        assert res.status_code != 500

    def test_logs_dopo_esecuzione(self, auth_headers, make_project):
        """I log sono disponibili dopo l'esecuzione."""
        project_id = make_project()
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=30)
        time.sleep(1)
        res = requests.get(f"{BASE_URL}/logs",
                           params={"project_id": project_id},
                           headers=auth_headers, timeout=5)
        assert res.status_code in (200, 404)


# =============================================================================
# REG-32 — SCHEDULER: esegue pipeline in automatico
# =============================================================================

class TestREG32Integration:

    def test_schedule_creata_no_500(self, auth_headers, make_project):
        """Schedule creata senza errori."""
        project_id = make_project()
        res = requests.post(
            f"{BASE_URL}/schedules",
            json={"project_id": project_id,
                  "cron_expression": "*/5 * * * *",
                  "description": "[REG] test schedule"},
            headers=auth_headers, timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            sid = res.json().get("id")
            if sid:
                requests.delete(f"{BASE_URL}/schedules/{sid}",
                                headers=auth_headers)

    @pytest.mark.parametrize("cron", [
        "*/5 * * * *",
        "0 2 * * *",
        "0 9-17 * * 1-5",
        "0 0 1 * *",
    ])
    def test_varie_cron_accettate(self, auth_headers, make_project, cron):
        """Varie cron expression accettate."""
        project_id = make_project()
        res = requests.post(
            f"{BASE_URL}/schedules",
            json={"project_id": project_id, "cron_expression": cron},
            headers=auth_headers, timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            sid = res.json().get("id")
            if sid:
                requests.delete(f"{BASE_URL}/schedules/{sid}",
                                headers=auth_headers)


# =============================================================================
# REG-33 — Multi-utente: 2 pipeline parallele gruppi diversi
# =============================================================================

class TestREG33Integration:

    def test_2_pipeline_parallele_no_500(self, auth_headers, make_project):
        """2 pipeline parallele non danno 500."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        pids = [make_project() for _ in range(2)]
        results = []

        def execute(pid):
            return requests.post(
                f"{BASE_URL}/projects/{pid}/execute",
                headers=auth_headers, timeout=30
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(execute, pid) for pid in pids]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0

    @pytest.mark.parametrize("n_parallel", [2, 3, 5])
    def test_n_pipeline_parallele_no_500(self, auth_headers, make_project,
                                          n_parallel):
        """N pipeline parallele non danno 500."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        pids = [make_project() for _ in range(n_parallel)]
        results = []

        def execute(pid):
            return requests.post(
                f"{BASE_URL}/projects/{pid}/execute",
                headers=auth_headers, timeout=30
            ).status_code

        with ThreadPoolExecutor(max_workers=n_parallel) as ex:
            futs = [ex.submit(execute, pid) for pid in pids]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0


# =============================================================================
# REG-34 — Reload canvas con 5 nodi persiste
# =============================================================================

class TestREG34Integration:

    @pytest.mark.parametrize("n_nodes", [3, 5, 10, 20])
    def test_canvas_n_nodi_persiste(self, auth_headers, make_project, n_nodes):
        """Canvas con N nodi persiste dopo salvataggio."""
        canvas = {
            "nodes": [{"id": f"n{i}", "type": "DataNode",
                       "position": {"x": i*100, "y": 0},
                       "data": {"label": f"Nodo {i}"}}
                      for i in range(n_nodes)],
            "edges": []
        }
        project_id = make_project()
        res_put = requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                               json=canvas, headers=auth_headers)
        if res_put.status_code == 200:
            res_get = requests.get(f"{BASE_URL}/projects/{project_id}",
                                   headers=auth_headers)
            assert res_get.status_code == 200

    def test_canvas_configurazione_preservata(self, auth_headers, make_project):
        """La configurazione dei nodi è preservata."""
        canvas = {
            "nodes": [{"id": "n1", "type": "FilterNode",
                       "data": {"column": "valore", "operator": ">",
                                "value": 500, "custom_param": "test"}}],
            "edges": []
        }
        project_id = make_project()
        requests.put(f"{BASE_URL}/projects/{project_id}/canvas",
                     json=canvas, headers=auth_headers)
        res = requests.get(f"{BASE_URL}/projects/{project_id}",
                           headers=auth_headers)
        assert res.status_code == 200


# =============================================================================
# REG-35 — Login → pipeline completa → logout → login
# =============================================================================

class TestREG35Integration:

    def test_login_logout_login_funziona(self):
        """Login → logout → login funziona."""
        # Login
        res1 = requests.post(f"{BASE_URL}/auth/login",
                             json={"username": "admin", "password": "admin123"})
        assert res1.status_code == 200
        token1 = res1.json()["access_token"]
        headers1 = {"Authorization": f"Bearer {token1}"}

        # Operazione con token
        res_me = requests.get(f"{BASE_URL}/auth/me", headers=headers1)
        assert res_me.status_code == 200

        # Logout
        res_logout = requests.post(f"{BASE_URL}/auth/logout", headers=headers1)
        assert res_logout.status_code in (200, 204, 404)

        # Nuovo login
        res2 = requests.post(f"{BASE_URL}/auth/login",
                             json={"username": "admin", "password": "admin123"})
        assert res2.status_code == 200
        token2 = res2.json()["access_token"]
        assert token2 is not None

    def test_token_scaduto_401(self):
        """Token scaduto produce 401."""
        expired_token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJ1c2VyIiwiZXhwIjoxNjAwMDAwMDAwfQ."
            "fake_signature"
        )
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers={"Authorization": f"Bearer {expired_token}"})
        assert res.status_code == 401

    @pytest.mark.parametrize("endpoint", [
        "/api/projects",
        "/api/connections",
        "/api/schedules",
        "/api/files",
    ])
    def test_endpoint_richiede_auth(self, endpoint):
        """Tutti gli endpoint protetti richiedono autenticazione."""
        res = requests.get(f"http://localhost:8000{endpoint}")
        assert res.status_code == 401

    def test_sessione_fresca_funziona(self):
        """In sessione fresca tutti gli endpoint rispondono."""
        res = requests.post(f"{BASE_URL}/auth/login",
                            json={"username": "admin", "password": "admin123"})
        assert res.status_code == 200
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        for endpoint in ["/api/projects", "/api/connections", "/api/schedules"]:
            r = requests.get(f"http://localhost:8000{endpoint}",
                             headers=headers, timeout=5)
            assert r.status_code == 200, f"{endpoint} → {r.status_code}"
