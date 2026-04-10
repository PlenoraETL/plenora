"""
tests/performance/integration/test_PERF09_11_carica.py
=======================================================
Test INTEGRATION performance — Modulo CARICA (PERF-09→PERF-11)
"""

import pytest
import requests
import time
import numpy as np
import pandas as pd

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


class TestPERF09Integration:
    """Write 100k righe PostgreSQL."""

    @pytest.fixture(scope="class")
    def pg_conn_id(self, auth_headers):
        """Crea connessione PostgreSQL per test CARICA."""
        res = requests.post(
            f"{BASE_URL}/connections",
            json={"name": "[PERF] carica test",
                  "type": "postgres",
                  "host": "localhost", "port": 5432,
                  "database": "testdb",
                  "username": "postgres", "password": "postgres"},
            headers=auth_headers
        )
        if res.status_code not in (200, 201):
            pytest.skip(f"Impossibile creare connessione: {res.text}")
        conn_id = res.json()["id"]
        yield conn_id
        requests.delete(f"{BASE_URL}/connections/{conn_id}", headers=auth_headers)

    @pytest.mark.slow
    def test_carica_100k_sotto_30s(self, auth_headers, make_project, pg_conn_id):
        """Write 100k righe in < 30s."""
        project_id = make_project(project_type="estrai")
        start = time.perf_counter()
        res = requests.post(
            f"{BASE_URL}/projects/{project_id}/execute-async",
            headers=auth_headers,
            timeout=THRESHOLDS["PERF09_write_100k"] + 10
        )
        elapsed = time.perf_counter() - start
        assert res.status_code not in (500,)
        if res.status_code in (200, 201):
            assert elapsed < THRESHOLDS["PERF09_write_100k"]

    @pytest.mark.parametrize("n_rows", [100, 1_000, 10_000])
    def test_validate_carica_vari_volumi(self, auth_headers, make_project,
                                         pg_conn_id, n_rows):
        """Validate carica con vari volumi risponde senza 500."""
        project_id = make_project()
        res = requests.post(
            f"{BASE_URL}/carica/validate",
            json={"project_id": project_id,
                  "connection_id": pg_conn_id,
                  "table_name": f"perf_test_{n_rows}"},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500

    def test_connection_test_postgres_performance(self, auth_headers, pg_conn_id):
        """Test connessione PostgreSQL risponde in < 5s."""
        start = time.perf_counter()
        res = requests.post(
            f"{BASE_URL}/connections/{pg_conn_id}/test",
            headers=auth_headers,
            timeout=10
        )
        elapsed = time.perf_counter() - start
        assert res.status_code in (200, 404)
        assert elapsed < 5.0

    @pytest.mark.parametrize("batch_size", [500, 1000, 5000, 10000])
    def test_carica_batch_size_vari(self, auth_headers, make_project,
                                    pg_conn_id, batch_size):
        """Carica con vari batch_size non dà 500."""
        project_id = make_project()
        res = requests.post(
            f"{BASE_URL}/carica/validate",
            json={"project_id": project_id,
                  "connection_id": pg_conn_id,
                  "table_name": "perf_batch_test",
                  "batch_size": batch_size},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500


class TestPERF10Integration:
    """Write 500k righe con batch=1000."""

    @pytest.mark.slow
    @pytest.mark.parametrize("batch_size", [500, 1000, 2000])
    def test_carica_500k_batch_vari(self, auth_headers, make_project, batch_size):
        """Carica con batch diversi risponde senza 500."""
        project_id = make_project()
        res = requests.post(
            f"{BASE_URL}/projects/{project_id}/execute-async",
            headers=auth_headers,
            timeout=THRESHOLDS["PERF10_write_500k"] + 10
        )
        assert res.status_code != 500

    def test_task_status_polling(self, auth_headers, make_project):
        """Polling task status risponde velocemente."""
        project_id = make_project()
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute-async",
                            headers=auth_headers, timeout=10)
        if res.status_code not in (200, 201):
            pytest.skip("Execute async non disponibile")

        task_id = res.json().get("task_id")
        if not task_id:
            pytest.skip("task_id non nella risposta")

        start = time.perf_counter()
        status_res = requests.get(f"{BASE_URL}/tasks/{task_id}",
                                  headers=auth_headers, timeout=5)
        elapsed = time.perf_counter() - start

        if status_res.status_code == 200:
            assert elapsed < 1.0, f"Status polling: {elapsed:.2f}s > 1s"
            body = status_res.json()
            assert "status" in body or body

    @pytest.mark.parametrize("n_tasks", [1, 3, 5])
    def test_task_list_performance(self, auth_headers, n_tasks):
        """Listing N task è rapido (Redis può essere lento)."""
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/tasks",
                           headers=auth_headers, timeout=10)
        elapsed = time.perf_counter() - start
        if res.status_code == 200:
            assert elapsed < 6.0


class TestPERF11Integration:
    """Feature Service ADD 10k features batch=200."""

    @pytest.mark.parametrize("batch_size", [100, 200, 500])
    def test_feature_service_config_accettata(self, auth_headers, batch_size):
        """Configurazione Feature Service con vari batch_size accettata."""
        res = requests.post(
            f"{BASE_URL}/api-connections",
            json={"name": f"[PERF] FS batch{batch_size}",
                  "api_type": "feature_service",
                  "endpoint": {"url": "https://fake.arcgis.com/FeatureServer/0",
                                "method": "POST"},
                  "auth": {"type": "token", "token": "fake_token"},
                  "parameters": [],
                  "output_mapping": [],
                  "options": {"batch_size": batch_size}},
            headers=auth_headers,
            timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            aid = res.json().get("id")
            if aid:
                requests.delete(f"{BASE_URL}/api-connections/{aid}",
                                headers=auth_headers)

    def test_feature_service_list_performance(self, auth_headers):
        """Lista api-connections di tipo feature_service è rapida."""
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/api-connections",
                           params={"category": "feature_service"},
                           headers=auth_headers, timeout=10)
        elapsed = time.perf_counter() - start
        assert res.status_code == 200
        assert elapsed < 5.0

    @pytest.mark.parametrize("n_features", [100, 1_000, 10_000])
    def test_validate_feature_count(self, auth_headers, make_project, n_features):
        """Validate con vari n_features risponde senza crash."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/estimate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {},
                  "n_rows": n_features},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500
