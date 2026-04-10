"""
tests/error_handling/integration/test_ERR_concurrency.py
=========================================================
Test di concorrenza e stress — integration (backend live)
"""

import pytest
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

pytestmark = [pytest.mark.integration, pytest.mark.security]

BASE_URL = "http://localhost:8000/api"


class TestLoginConcorrenza:

    def test_10_login_simultanei_tutti_200(self, auth_headers):
        results = []
        def do():
            return requests.post(f"{BASE_URL}/auth/login",
                                 json={"username": "admin", "password": "admin123"}).status_code
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())
        assert all(s == 200 for s in results)

    def test_10_me_simultanee_tutte_200(self, auth_headers):
        results = []
        def do():
            return requests.get(f"{BASE_URL}/auth/me", headers=auth_headers).status_code
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())
        assert all(s == 200 for s in results)

    @pytest.mark.parametrize("n_workers", [5, 10, 20])
    def test_n_login_simultanei(self, n_workers):
        results = []
        def do():
            return requests.post(f"{BASE_URL}/auth/login",
                                 json={"username": "admin", "password": "admin123"}).status_code
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(do) for _ in range(n_workers)]
            for f in as_completed(futs):
                results.append(f.result())
        failed = [s for s in results if s != 200]
        assert len(failed) == 0, f"{len(failed)}/{n_workers} login falliti: {failed}"

    def test_backend_stabile_dopo_carico(self, auth_headers):
        def do():
            requests.post(f"{BASE_URL}/auth/login",
                          json={"username": "admin", "password": "admin123"})
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                f.result()
        res = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers)
        assert res.status_code == 200


class TestProjectsConcorrenza:

    def test_10_get_projects_simultanee(self, auth_headers):
        results = []
        def do():
            return requests.get(f"{BASE_URL}/projects", headers=auth_headers).status_code
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())
        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0

    def test_create_delete_project_concorrente(self, auth_headers):
        """Creazione e cancellazione concorrente non deve corrompere i dati."""
        created = []
        errors = []

        def do_create():
            try:
                res = requests.post(f"{BASE_URL}/projects",
                                    json={"name": f"[STRESS] proj",
                                          "project_type": "estrai",
                                          "canvas": {"nodes": [], "edges": []}},
                                    headers=auth_headers)
                if res.status_code == 200:
                    created.append(res.json()["metadata"]["id"])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_create) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Cleanup
        for pid in created:
            requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)

        assert len(errors) == 0


class TestConnectionsConcorrenza:

    def test_10_get_connections_simultanee(self, auth_headers):
        results = []
        def do():
            return requests.get(f"{BASE_URL}/connections", headers=auth_headers).status_code
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())
        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0


class TestSchedulesConcorrenza:

    def test_10_get_schedules_simultanee(self, auth_headers):
        results = []
        def do():
            return requests.get(f"{BASE_URL}/schedules", headers=auth_headers).status_code
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())
        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0


class TestLogsConcorrenza:

    def test_10_get_logs_simultanee(self, auth_headers):
        results = []
        def do():
            return requests.get(f"{BASE_URL}/logs", headers=auth_headers).status_code
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do) for _ in range(10)]
            for f in as_completed(futs):
                results.append(f.result())
        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0

    @pytest.mark.parametrize("n_workers", [5, 10])
    def test_n_letture_log_simultanee(self, n_workers, auth_headers):
        results = []
        def do():
            return requests.get(f"{BASE_URL}/logs",
                                params={"limit": 10},
                                headers=auth_headers).status_code
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(do) for _ in range(n_workers)]
            for f in as_completed(futs):
                results.append(f.result())
        assert all(s == 200 for s in results)


class TestStressIntegration:

    @pytest.mark.slow
    def test_100_richieste_me_consecutive(self, auth_headers):
        """100 richieste consecutive a /me senza crash."""
        results = []
        for _ in range(100):
            res = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers)
            results.append(res.status_code)
        failed = [s for s in results if s != 200]
        assert len(failed) == 0, f"{len(failed)}/100 richieste fallite"

    @pytest.mark.slow
    @pytest.mark.parametrize("n", [10, 50])
    def test_n_richieste_projects_consecutive(self, n, auth_headers):
        for _ in range(n):
            res = requests.get(f"{BASE_URL}/projects", headers=auth_headers)
            assert res.status_code == 200

    def test_mixed_endpoints_no_crash(self, auth_headers):
        """Mix di endpoint diversi non causa crash."""
        endpoints = [
            f"{BASE_URL}/auth/me",
            f"{BASE_URL}/projects",
            f"{BASE_URL}/connections",
            f"{BASE_URL}/schedules",
            f"{BASE_URL}/logs",
        ]
        results = []

        def do(url):
            return requests.get(url, headers=auth_headers).status_code

        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = [ex.submit(do, ep) for ep in endpoints * 3]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0
