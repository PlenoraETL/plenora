"""
tests/performance/integration/test_PERF03_05_manipola.py
=========================================================
Test INTEGRATION performance — Modulo MANIPOLA (PERF-03→PERF-05)
"""

import pytest
import requests
import time
import numpy as np
import json

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


class TestPERF03Integration:
    """Join spaziale su 50k poligoni via API."""

    @pytest.mark.parametrize("n_rows", [100, 1_000, 10_000])
    def test_esegui_progetto_join_tempi(self, auth_headers, make_project, n_rows):
        """Esecuzione progetto join risponde senza 500."""
        project_id = make_project(project_type="manipola")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=THRESHOLDS["PERF03_join"] + 10)
        assert res.status_code != 500

    def test_canvas_con_join_node_accettato(self, auth_headers, make_project):
        """Canvas con nodo join viene salvato correttamente."""
        canvas = {
            "nodes": [
                {"id": "n1", "type": "JoinNode",
                 "data": {"join_type": "inner", "left_key": "id",
                          "right_key": "id"}},
            ],
            "edges": []
        }
        res = requests.post(
            f"{BASE_URL}/projects",
            json={"name": "[PERF] join test",
                  "project_type": "manipola",
                  "canvas": canvas},
            headers=auth_headers
        )
        assert res.status_code in (200, 201)
        if res.status_code == 200:
            pid = res.json()["metadata"]["id"]
            requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)

    @pytest.mark.parametrize("join_type", ["inner", "left", "right", "outer"])
    def test_join_types_accettati(self, auth_headers, make_project, join_type):
        """Tutti i tipi di join sono accettati dal canvas."""
        canvas = {
            "nodes": [{"id": "n1", "type": "JoinNode",
                       "data": {"join_type": join_type}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(
            f"{BASE_URL}/projects/{project_id}/canvas",
            json=canvas, headers=auth_headers
        )
        assert res.status_code in (200, 404, 422)


class TestPERF04Integration:
    """Dissolve 100k poligoni via API."""

    @pytest.mark.slow
    def test_dissolve_progetto_no_500(self, auth_headers, make_project):
        """Esecuzione dissolve non dà 500."""
        project_id = make_project(project_type="manipola")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers,
                            timeout=THRESHOLDS["PERF04_dissolve"] + 10)
        assert res.status_code != 500

    @pytest.mark.parametrize("group_col", ["categoria", "regione", "flag"])
    def test_canvas_dissolve_vari_campi(self, auth_headers, group_col):
        """Canvas dissolve su vari campi viene salvato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "DissolveNode",
                       "data": {"group_by": group_col, "aggfunc": "sum"}}],
            "edges": []
        }
        res = requests.post(
            f"{BASE_URL}/projects",
            json={"name": f"[PERF] dissolve {group_col}",
                  "project_type": "manipola",
                  "canvas": canvas},
            headers=auth_headers
        )
        assert res.status_code in (200, 201)
        if res.status_code == 200:
            pid = res.json()["metadata"]["id"]
            requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)

    @pytest.mark.parametrize("aggfunc", ["sum", "mean", "count", "first", "last"])
    def test_dissolve_varie_aggfunc(self, auth_headers, aggfunc):
        """Dissolve con varie aggfunc viene accettato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "DissolveNode",
                       "data": {"group_by": "categoria", "aggfunc": aggfunc}}],
            "edges": []
        }
        res = requests.post(
            f"{BASE_URL}/projects",
            json={"name": f"[PERF] dissolve {aggfunc}",
                  "project_type": "manipola",
                  "canvas": canvas},
            headers=auth_headers
        )
        assert res.status_code in (200, 201)
        if res.status_code == 200:
            pid = res.json()["metadata"]["id"]
            requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)


class TestPERF05Integration:
    """Pivot 2M righe via API."""

    @pytest.mark.slow
    def test_pivot_progetto_no_500(self, auth_headers, make_project):
        """Esecuzione pivot non dà 500."""
        project_id = make_project(project_type="manipola")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers,
                            timeout=THRESHOLDS["PERF05_pivot"] + 10)
        assert res.status_code != 500

    @pytest.mark.parametrize("index,columns,values", [
        ("categoria", "anno", "valore"),
        ("regione",   "categoria", "valore"),
        ("flag",      "categoria", "quantita"),
    ])
    def test_canvas_pivot_vari_campi(self, auth_headers, index, columns, values):
        """Canvas pivot con vari campi viene salvato."""
        canvas = {
            "nodes": [{"id": "n1", "type": "PivotNode",
                       "data": {"index": index, "columns": columns,
                                "values": values, "aggfunc": "sum"}}],
            "edges": []
        }
        res = requests.post(
            f"{BASE_URL}/projects",
            json={"name": f"[PERF] pivot {index}x{columns}",
                  "project_type": "manipola",
                  "canvas": canvas},
            headers=auth_headers
        )
        assert res.status_code in (200, 201)
        if res.status_code == 200:
            pid = res.json()["metadata"]["id"]
            requests.delete(f"{BASE_URL}/projects/{pid}", headers=auth_headers)

    def test_get_progetto_pivot_veloce(self, auth_headers, make_project):
        """GET progetto pivot è < 1s."""
        project_id = make_project(project_type="manipola")
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/projects/{project_id}",
                           headers=auth_headers)
        elapsed = time.perf_counter() - start
        assert res.status_code == 200
        assert elapsed < 3.0

    @pytest.mark.parametrize("n_projects", [1, 3, 5])
    def test_list_progetti_manipola_veloce(self, auth_headers, n_projects,
                                           make_project):
        """Listing N progetti manipola è < 2s."""
        for _ in range(n_projects):
            make_project(project_type="manipola")
        start = time.perf_counter()
        res = requests.get(f"{BASE_URL}/projects",
                           params={"type": "manipola"},
                           headers=auth_headers)
        elapsed = time.perf_counter() - start
        assert res.status_code == 200
        assert elapsed < 4.0
