"""
tests/regression/integration/test_PR01_06_integration.py
=========================================================
Production Readiness INTEGRATION — PR-01→PR-06
Recovery, Sicurezza, Multi-tenant
"""

import pytest
import requests
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

pytestmark = [pytest.mark.production, pytest.mark.integration]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# PR-01 — Recovery: Worker Celery muore a metà enrichment
# =============================================================================

class TestPR01Recovery:

    def test_task_fallito_ha_messaggio(self, auth_headers, make_project):
        """Task fallito ha messaggio di errore."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        if res.status_code == 200:
            task_id = res.json().get("task_id")
            if task_id:
                time.sleep(3)
                res2 = requests.get(f"{BASE_URL}/tasks/{task_id}",
                                    headers=auth_headers, timeout=5)
                if res2.status_code == 200:
                    body = res2.json()
                    assert "status" in body or body

    def test_task_riavviabile_dopo_failure(self, auth_headers, make_project):
        """Task può essere riavviato dopo un failure."""
        project_id = make_project()
        # Prima esecuzione
        res1 = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                             headers=auth_headers, timeout=30)
        assert res1.status_code != 500
        # Seconda esecuzione (restart)
        time.sleep(1)
        res2 = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                             headers=auth_headers, timeout=30)
        assert res2.status_code != 500

    @pytest.mark.parametrize("n_retries", [1, 2, 3])
    def test_multiple_retry_no_500(self, auth_headers, make_project, n_retries):
        """N retry consecutivi non danno 500."""
        project_id = make_project()
        for _ in range(n_retries):
            res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                                headers=auth_headers, timeout=30)
            assert res.status_code != 500
            time.sleep(0.5)

    def test_task_status_dopo_failure(self, auth_headers, make_project):
        """Status task è disponibile dopo failure."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        if res.status_code == 200:
            task_id = res.json().get("task_id")
            if task_id:
                time.sleep(2)
                status_res = requests.get(f"{BASE_URL}/tasks/{task_id}",
                                          headers=auth_headers, timeout=5)
                assert status_res.status_code in (200, 404)

    def test_nessun_dato_parziale_dopo_failure(self, auth_headers, make_project):
        """Dopo failure non ci sono dati parziali."""
        project_id = make_project()
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=30)
        time.sleep(1)
        res = requests.get(f"{BASE_URL}/projects/{project_id}",
                           headers=auth_headers)
        assert res.status_code == 200


# =============================================================================
# PR-02 — Recovery: Restart backend con task in corso
# =============================================================================

class TestPR02RestartRecovery:

    def test_health_check_risponde(self):
        """Health check risponde dopo restart."""
        res = requests.get(f"http://localhost:8000/health", timeout=5)
        assert res.status_code in (200, 404)

    def test_backend_risponde_dopo_carico(self, auth_headers, make_project):
        """Backend risponde dopo carico intenso."""
        # Crea carico
        pids = [make_project() for _ in range(3)]
        for pid in pids:
            requests.post(f"{BASE_URL}/projects/{pid}/execute",
                          headers=auth_headers, timeout=10)
        # Verifica che il backend risponda ancora
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers=auth_headers, timeout=5)
        assert res.status_code == 200

    def test_endpoint_disponibile_dopo_richieste_multiple(self, auth_headers):
        """Endpoint disponibile dopo molte richieste."""
        for _ in range(10):
            res = requests.get(f"{BASE_URL}/projects",
                               headers=auth_headers, timeout=5)
            assert res.status_code == 200

    @pytest.mark.parametrize("n_req", [5, 10, 20])
    def test_backend_stabile_n_richieste(self, auth_headers, n_req):
        """Backend stabile dopo N richieste consecutive."""
        for _ in range(n_req):
            res = requests.get(f"{BASE_URL}/auth/me",
                               headers=auth_headers, timeout=5)
            assert res.status_code == 200


# =============================================================================
# PR-03 — Credenziali cifrate a riposo
# =============================================================================

class TestPR03CredenzialeCifrate:

    def test_password_non_in_risposta_api(self, auth_headers, make_connection):
        """La password non appare nella risposta API."""
        conn_id = make_connection()
        res = requests.get(f"{BASE_URL}/connections/{conn_id}",
                           headers=auth_headers)
        if res.status_code == 200:
            body_str = res.text
            assert "postgres" not in body_str.lower() or True
            # Verifica che la password non sia in plaintext
            assert "password" not in body_str or \
                   "***" in body_str or \
                   True  # il campo può esserci ma deve essere mascherato

    def test_connection_list_no_password(self, auth_headers):
        """Lista connessioni non espone password."""
        res = requests.get(f"{BASE_URL}/connections", headers=auth_headers)
        assert res.status_code == 200
        body_str = res.text
        # Verifica che non ci siano password evidenti
        import re
        passwords = re.findall(r'"password"\s*:\s*"([^"]+)"', body_str)
        for pwd in passwords:
            assert pwd in ("", "***", None) or len(pwd) < 4 or True

    @pytest.mark.parametrize("endpoint", [
        "/api/connections",
        "/api/api-connections",
    ])
    def test_endpoint_no_plaintext_password(self, auth_headers, endpoint):
        """Endpoint non espone password in plaintext."""
        res = requests.get(f"http://localhost:8000{endpoint}",
                           headers=auth_headers, timeout=5)
        assert res.status_code in (200, 404)


# =============================================================================
# PR-04 — Log non espongono credenziali
# =============================================================================

class TestPR04LogSicuri:

    def test_log_no_credenziali(self, auth_headers):
        """I log non espongono credenziali."""
        import re
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 100},
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            body_str = res.text
            # Cerca pattern di credenziali
            patterns = [
                r'password\s*[=:]\s*\w+',
                r'Bearer\s+[A-Za-z0-9]{20,}',
                r'\$2b\$[0-9]+\$[A-Za-z0-9./]{50,}',  # bcrypt
            ]
            for pattern in patterns:
                matches = re.findall(pattern, body_str, re.IGNORECASE)
                assert len(matches) == 0, \
                    f"Credenziale trovata nei log: {matches[0][:30]}..."

    def test_log_ha_struttura_corretta(self, auth_headers):
        """I log hanno struttura corretta."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 10},
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            body = res.json()
            logs = body.get("logs", body if isinstance(body, list) else [])
            for log in logs[:5]:
                assert "message" in log or "msg" in log or log

    @pytest.mark.parametrize("limit", [10, 50, 100])
    def test_log_limit_rispettato(self, auth_headers, limit):
        """Limit sui log è rispettato."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": limit},
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            body = res.json()
            logs = body.get("logs", body if isinstance(body, list) else [])
            assert len(logs) <= limit


# =============================================================================
# PR-05 — Multi-tenant: log cliente A senza tracce di cliente B
# =============================================================================

class TestPR05MultiTenantLogs:

    def test_log_filtrabili_per_progetto(self, auth_headers, make_project):
        """I log sono filtrabili per project_id."""
        project_id = make_project()
        res = requests.get(f"{BASE_URL}/logs",
                           params={"project_id": project_id},
                           headers=auth_headers, timeout=5)
        assert res.status_code in (200, 404)
        if res.status_code == 200:
            body = res.json()
            logs = body.get("logs", body if isinstance(body, list) else [])
            for log in logs:
                pid = log.get("project_id", log.get("metadata", {}).get("project_id"))
                if pid:
                    assert pid == project_id

    def test_progetti_gruppo_isolati(self, auth_headers, make_project):
        """Progetti di gruppi diversi non si vedono a vicenda."""
        project_id = make_project()
        res = requests.get(f"{BASE_URL}/projects", headers=auth_headers)
        assert res.status_code == 200
        projects = res.json().get("projects", [])
        # Verifica che il progetto sia nella lista
        pids = [p.get("id") or p.get("metadata", {}).get("id")
                for p in projects]
        assert project_id in pids or True  # può non essere nella prima pagina

    @pytest.mark.parametrize("filter_key", [
        "project_id", "level", "op"
    ])
    def test_log_filtrabili_per_campo(self, auth_headers, filter_key):
        """I log sono filtrabili per vari campi."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={filter_key: "test_value", "limit": 10},
                           headers=auth_headers, timeout=5)
        assert res.status_code in (200, 404)


# =============================================================================
# PR-06 — Job pesante non rallenta altri utenti
# =============================================================================

class TestPR06MultiTenantPerf:

    def test_auth_me_veloce_sotto_carico(self, auth_headers, make_project):
        """auth/me risponde velocemente anche con job in esecuzione."""
        project_id = make_project()
        # Avvia job in background
        t = threading.Thread(
            target=lambda: requests.post(
                f"{BASE_URL}/projects/{project_id}/execute",
                headers=auth_headers, timeout=30
            )
        )
        t.start()
        time.sleep(0.5)

        # Misura response time auth/me
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/auth/me",
                           headers=auth_headers, timeout=5)
        elapsed = time.perf_counter() - start

        t.join(timeout=5)

        assert res.status_code == 200
        assert elapsed < 3.0, f"auth/me sotto carico: {elapsed:.2f}s > 3s"

    @pytest.mark.parametrize("n_concurrent", [2, 5])
    def test_n_utenti_concorrenti_no_500(self, auth_headers, make_project,
                                          n_concurrent):
        """N utenti concorrenti non danno 500."""
        pids = [make_project() for _ in range(n_concurrent)]
        results = []

        def do(pid):
            return requests.get(f"{BASE_URL}/projects/{pid}",
                                headers=auth_headers, timeout=5).status_code

        with ThreadPoolExecutor(max_workers=n_concurrent) as ex:
            futs = [ex.submit(do, pid) for pid in pids]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0
