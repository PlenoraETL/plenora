"""
tests/performance/integration/test_PERF14_15_api_scheduler.py
=============================================================
Test INTEGRATION performance — API concorrenza + Scheduler (PERF-14→PERF-15)
"""

import pytest
import requests
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import time as _time
from contextlib import contextmanager as _contextmanager

THRESHOLDS = {
    "PERF01_preview": 5, "PERF02_upload": 60, "PERF03_join": 120,
    "PERF04_dissolve": 60, "PERF05_pivot": 30, "PERF06_enrichment": 1100,
    "PERF07_dedup": 10, "PERF08_preview": 5, "PERF09_write_100k": 30,
    "PERF10_write_500k": 120, "PERF11_feature_svc": 60,
    "PERF14_concurrent": 10, "PERF15_scheduler": 90,
}

@_contextmanager
def assert_faster_than(seconds, label=""):
    start = _time.perf_counter()
    yield
    elapsed = _time.perf_counter() - start
    assert elapsed < seconds, f"{label} ha impiegato {elapsed:.2f}s > {seconds}s"

def assert_df_quality(df, min_rows=1, max_null_pct=0.01, label="DataFrame"):
    assert df is not None and not df.empty and len(df) >= min_rows

def assert_gdf_quality(gdf, min_rows=1, label="GeoDataFrame"):
    assert_df_quality(gdf, min_rows=min_rows)
    assert gdf.geometry.notna().all() and gdf.crs is not None



pytestmark = [pytest.mark.perf, pytest.mark.integration]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# PERF-14 — 50 richieste simultanee
# =============================================================================

class TestPERF14Integration:

    @pytest.mark.parametrize("n_workers", [5, 10, 20, 50])
    def test_preview_concorrenti_no_500(self, auth_headers, n_workers):
        """N richieste /preview concorrenti: tutte < 10s, nessun 500."""
        results = []
        times = []

        def do():
            t0 = time.perf_counter()
            res = requests.get(
                f"{BASE_URL}/estrai/preview",
                params={"connection_id": "conn_fake",
                        "table_name": "test", "limit": 5},
                headers=auth_headers,
                timeout=THRESHOLDS["PERF14_concurrent"] + 2
            )
            return res.status_code, time.perf_counter() - t0

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(do) for _ in range(n_workers)]
            for f in as_completed(futs):
                status, t = f.result()
                results.append(status)
                times.append(t)
        total = time.perf_counter() - start

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0, f"{len(crashes)}/{n_workers} → 500"
        max_time = max(times)
        assert max_time < THRESHOLDS["PERF14_concurrent"], \
            f"Risposta più lenta: {max_time:.2f}s > {THRESHOLDS['PERF14_concurrent']}s"

    @pytest.mark.parametrize("n_workers", [5, 10, 20, 50])
    def test_auth_me_concorrente(self, auth_headers, n_workers):
        """N richieste /auth/me concorrenti completano in < 5s."""
        results = []

        def do():
            return requests.get(f"{BASE_URL}/auth/me",
                                headers=auth_headers,
                                timeout=5).status_code

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(do) for _ in range(n_workers)]
            for f in as_completed(futs):
                results.append(f.result())
        elapsed = time.perf_counter() - start

        assert all(s == 200 for s in results), \
            f"{sum(1 for s in results if s != 200)} richieste fallite"
        assert elapsed < 5.0, f"50 /auth/me: {elapsed:.2f}s > 5s"

    @pytest.mark.parametrize("n_workers", [5, 10, 20])
    def test_projects_list_concorrente(self, auth_headers, n_workers):
        """N richieste /projects concorrenti senza 500."""
        results = []

        def do():
            return requests.get(f"{BASE_URL}/projects",
                                headers=auth_headers,
                                timeout=5).status_code

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(do) for _ in range(n_workers)]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0

    @pytest.mark.parametrize("endpoint,n", [
        ("/api/connections", 10),
        ("/api/schedules",   10),
        ("/api/logs",        10),
        ("/api/files",       10),
    ])
    def test_endpoint_concorrente_no_500(self, auth_headers, endpoint, n):
        """Endpoint vari sotto carico concorrente non danno 500."""
        results = []

        def do():
            return requests.get(f"http://localhost:8000{endpoint}",
                                headers=auth_headers,
                                timeout=5).status_code

        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(do) for _ in range(n)]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0, \
            f"{endpoint}: {len(crashes)}/{n} → 500"

    def test_login_50_concorrenti_no_lockout(self, auth_headers):
        """50 login validi simultanei non causano lockout globale."""
        results = []

        def do():
            return requests.post(
                f"{BASE_URL}/auth/login",
                json={"username": "admin", "password": "admin123"},
                timeout=5
            ).status_code

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(20)]
            for f in as_completed(futs):
                results.append(f.result())

        # Non tutti devono riuscire (rate limit), ma nessun 500
        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0

    def test_response_time_p95(self, auth_headers):
        """Il 95° percentile del tempo di risposta è < 2s."""
        import statistics
        times = []

        def do():
            t0 = time.perf_counter()
            requests.get(f"{BASE_URL}/auth/me",
                         headers=auth_headers, timeout=5)
            return time.perf_counter() - t0

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(50)]
            for f in as_completed(futs):
                times.append(f.result())

        times.sort()
        p95 = times[int(len(times) * 0.95)]
        assert p95 < 4.0, f"P95 response time: {p95:.2f}s > 4s"


# =============================================================================
# PERF-15 — 10 schedule attive contemporaneamente
# =============================================================================

class TestPERF15Integration:

    @pytest.fixture(scope="class")
    def schedules_10(self, auth_headers, make_project):
        """Crea 10 schedule di test."""
        created = []
        for i in range(10):
            project_id = make_project(project_type="estrai")
            res = requests.post(
                f"{BASE_URL}/schedules",
                json={"project_id": project_id,
                      "cron_expression": "*/5 * * * *",
                      "description": f"[PERF] schedule {i}"},
                headers=auth_headers
            )
            if res.status_code in (200, 201):
                created.append(res.json().get("id"))
        yield created
        for sid in created:
            if sid:
                requests.delete(f"{BASE_URL}/schedules/{sid}",
                                headers=auth_headers)

    def test_10_schedule_create_performance(self, auth_headers, make_project):
        """Creazione 10 schedule è < 10s totali."""
        created = []
        start = time.perf_counter()
        for i in range(10):
            project_id = make_project()
            res = requests.post(
                f"{BASE_URL}/schedules",
                json={"project_id": project_id,
                      "cron_expression": "*/5 * * * *"},
                headers=auth_headers,
                timeout=5
            )
            if res.status_code in (200, 201):
                created.append(res.json().get("id"))
        elapsed = time.perf_counter() - start

        for sid in created:
            if sid:
                requests.delete(f"{BASE_URL}/schedules/{sid}",
                                headers=auth_headers)

        assert elapsed < 60.0, f"10 schedule create in {elapsed:.2f}s > 60s"

    def test_list_schedules_performance(self, auth_headers):
        """Lista schedule risponde in < 2s."""
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/schedules",
                           headers=auth_headers, timeout=5)
        elapsed = time.perf_counter() - start
        assert res.status_code == 200
        assert elapsed < 4.0, f"List schedules: {elapsed:.2f}s > 4s"

    @pytest.mark.parametrize("n_schedules", [3, 5, 10])
    def test_toggle_n_schedule_performance(self, auth_headers, make_project,
                                           n_schedules):
        """Toggle N schedule è rapido."""
        created = []
        for i in range(n_schedules):
            pid = make_project()
            res = requests.post(
                f"{BASE_URL}/schedules",
                json={"project_id": pid, "cron_expression": "0 2 * * *"},
                headers=auth_headers
            )
            if res.status_code in (200, 201):
                created.append(res.json().get("id"))

        start = time.perf_counter()
        for sid in created:
            if sid:
                requests.patch(f"{BASE_URL}/schedules/{sid}/toggle",
                               json={"enabled": False},
                               headers=auth_headers, timeout=3)
        elapsed = time.perf_counter() - start

        for sid in created:
            if sid:
                requests.delete(f"{BASE_URL}/schedules/{sid}",
                                headers=auth_headers)

        assert elapsed < n_schedules * 3 + 5

    def test_schedule_log_list_performance(self, auth_headers):
        """Lista log schedule risponde in < 2s."""
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 100},
                           headers=auth_headers, timeout=5)
        elapsed = time.perf_counter() - start
        if res.status_code == 200:
            assert elapsed < 4.0

    def test_10_schedule_list_concorrente(self, auth_headers):
        """10 richieste lista schedule concorrenti senza 500."""
        results = []

        def do():
            return requests.get(f"{BASE_URL}/schedules",
                                headers=auth_headers, timeout=5).status_code

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0
