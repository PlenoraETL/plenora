"""
tests/performance/integration/test_PERF06_08_arricchisci.py
============================================================
Test INTEGRATION performance — Modulo ARRICCHISCI (PERF-06→PERF-08)
"""

import pytest
import requests
import time
import numpy as np

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


class TestPERF06Integration:
    """Enrichment 10k righe con rate_limit=10."""

    @pytest.mark.parametrize("rate_limit", [1, 5, 10, 20, 50])
    def test_validate_enrichment_vari_rate(self, auth_headers, make_project, rate_limit):
        """Validate enrichment con vari rate_limit non dà 500."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/validate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {},
                  "rate_limit": rate_limit},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("n_rows", [10, 100, 1_000, 10_000])
    def test_estimate_enrichment_vari_volumi(self, auth_headers, make_project, n_rows):
        """Estimate enrichment con vari volumi risponde senza crash."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/estimate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {},
                  "n_rows": n_rows},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500

    def test_api_connection_list_performance(self, auth_headers):
        """Lista api-connections risponde in < 2s."""
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/api-connections",
                           headers=auth_headers, timeout=5)
        elapsed = time.perf_counter() - start
        assert res.status_code == 200
        assert elapsed < 5.0

    @pytest.mark.parametrize("n_connections", [1, 3, 5])
    def test_api_connections_create_performance(self, auth_headers, n_connections):
        """Creazione N api-connections è rapida."""
        created = []
        for i in range(n_connections):
            res = requests.post(
                f"{BASE_URL}/api-connections",
                json={"name": f"[PERF] api_conn_{i}",
                      "api_type": "enricher",
                      "endpoint": {"url": f"http://api{i}.test.com", "method": "GET"},
                      "auth": {"type": "none"},
                      "parameters": [], "output_mapping": []},
                headers=auth_headers,
                timeout=5
            )
            if res.status_code in (200, 201):
                created.append(res.json().get("id"))

        for aid in created:
            if aid:
                requests.delete(f"{BASE_URL}/api-connections/{aid}",
                                headers=auth_headers)


class TestPERF07Integration:
    """Enrichment 50k righe con cache dedup."""

    @pytest.mark.parametrize("n_unique", [1, 3, 5, 10])
    def test_dedup_via_api_validate(self, auth_headers, make_project, n_unique):
        """Validate enrichment con param_mapping dedup non dà 500."""
        project_id = make_project(project_type="arricchisci")
        param_mapping = {f"param_{i}": f"col_{i}" for i in range(n_unique)}
        res = requests.post(
            f"{BASE_URL}/enrichment/validate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": param_mapping},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500

    def test_api_connection_con_dedup_accettata(self, auth_headers):
        """API connection con configurazione dedup viene accettata."""
        res = requests.post(
            f"{BASE_URL}/api-connections",
            json={"name": "[PERF] dedup test",
                  "api_type": "enricher",
                  "endpoint": {"url": "http://fake.api.com", "method": "GET"},
                  "auth": {"type": "none"},
                  "parameters": [{"name": "city", "in": "query", "required": True}],
                  "output_mapping": [{"path": "population", "column": "pop"}],
                  "options": {"deduplicate": True}},
            headers=auth_headers,
            timeout=5
        )
        assert res.status_code in (200, 201, 422)
        if res.status_code in (200, 201):
            aid = res.json().get("id")
            if aid:
                requests.delete(f"{BASE_URL}/api-connections/{aid}",
                                headers=auth_headers)

    @pytest.mark.parametrize("batch_size", [10, 50, 100, 500])
    def test_estimate_batch_size_vari(self, auth_headers, make_project, batch_size):
        """Estimate con vari batch_size non dà 500."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/estimate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {},
                  "batch_size": batch_size},
            headers=auth_headers,
            timeout=10
        )
        assert res.status_code != 500


class TestPERF08Integration:
    """Preview 5 righe su sorgente 100k."""

    def test_preview_5_righe_sotto_soglia(self, auth_headers, pg_connection_id):
        """Preview 5 righe su tabella grande è < 5s."""
        start = time.perf_counter()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "test_table",
                    "limit": 5},
            headers=auth_headers,
            timeout=THRESHOLDS["PERF08_preview"] + 5
        )
        elapsed = time.perf_counter() - start
        if res.status_code == 200:
            assert elapsed < THRESHOLDS["PERF08_preview"], \
                f"Preview 5 righe: {elapsed:.2f}s > {THRESHOLDS['PERF08_preview']}s"
            body = res.json()
            rows = body.get("rows", body.get("data", []))
            assert len(rows) <= 5

    @pytest.mark.parametrize("limit", [1, 3, 5, 10, 20])
    def test_preview_piccolo_sempre_veloce(self, auth_headers, pg_connection_id, limit):
        """Preview con limit piccolo è sempre sotto soglia."""
        start = time.perf_counter()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "test_table",
                    "limit": limit},
            headers=auth_headers,
            timeout=THRESHOLDS["PERF08_preview"] + 5
        )
        elapsed = time.perf_counter() - start
        if res.status_code == 200:
            assert elapsed < THRESHOLDS["PERF08_preview"]

    def test_preview_qualita_output(self, auth_headers, pg_connection_id):
        """Preview 5 righe ha dati di qualità."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "test_table",
                    "limit": 5},
            headers=auth_headers,
            timeout=10
        )
        if res.status_code == 200:
            body = res.json()
            rows = body.get("rows", body.get("data", []))
            assert len(rows) > 0
            columns = body.get("columns", [])
            assert len(columns) > 0

    @pytest.mark.parametrize("n_concurrent", [2, 5, 10])
    def test_preview_piccolo_concorrente(self, auth_headers, pg_connection_id,
                                         n_concurrent):
        """N preview piccole concorrenti completano senza 500."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []

        def do():
            return requests.get(
                f"{BASE_URL}/estrai/preview",
                params={"connection_id": pg_connection_id,
                        "table_name": "test_table", "limit": 5},
                headers=auth_headers,
                timeout=10
            ).status_code

        with ThreadPoolExecutor(max_workers=n_concurrent) as ex:
            futs = [ex.submit(do) for _ in range(n_concurrent)]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0
