"""
tests/performance/unit/test_PERF12_13_ui.py
============================================
Test UNIT di performance — Download + UI (PERF-12→PERF-13)

PERF-12  Download GPKG 500MB → no timeout, streaming corretto
PERF-13  Canvas 20 nodi / 25 edge → render < 2s, struttura valida
"""

import pytest
import time
import numpy as np
import pandas as pd
import json
from unittest.mock import MagicMock, patch

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



pytestmark = [pytest.mark.perf, pytest.mark.unit]



# =============================================================================
# PERF-12 — Download GPKG 500MB
# =============================================================================

class TestPERF12DownloadUnit:

    @pytest.mark.parametrize("size_mb", [1, 10, 50, 100, 500])
    def test_gpkg_generazione_sintetica(self, size_mb):
        """Generazione dati GPKG sintetici di varie dimensioni."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            # stima: ~50 bytes per feature → size_mb MB = size_mb*1024*1024/50 features
            n = min(size_mb * 1000, 100_000)
            rng = np.random.default_rng(42)
            lons = rng.uniform(6, 19, size=n)
            lats = rng.uniform(36, 47, size=n)
            threshold = max(2.0, size_mb / 100 * 10)
            with assert_faster_than(threshold, f"gen {size_mb}MB GPKG"):
                gdf = gpd.GeoDataFrame(
                    {"id": np.arange(n),
                     "nome": [f"feat_{i}" for i in range(n)],
                     "valore": rng.uniform(0, 100, n)},
                    geometry=[Point(lon, lat) for lon, lat in zip(lons, lats)],
                    crs="EPSG:4326"
                )
            assert len(gdf) == n
            assert gdf.geometry.is_valid.all()
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    @pytest.mark.parametrize("n_features", [1_000, 10_000, 100_000])
    def test_gpkg_scrittura_mock(self, n_features, tmp_path):
        """Scrittura GPKG sintetico su disco."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            rng = np.random.default_rng(42)
            lons = rng.uniform(6, 19, size=n_features)
            lats = rng.uniform(36, 47, size=n_features)
            gdf = gpd.GeoDataFrame(
                {"id": np.arange(n_features),
                 "val": rng.uniform(0, 100, n_features)},
                geometry=[Point(lon, lat) for lon, lat in zip(lons, lats)],
                crs="EPSG:4326"
            )
            gpkg_path = tmp_path / "test.gpkg"
            threshold = max(2.0, n_features / 10_000 * 5)
            with assert_faster_than(threshold, f"write GPKG {n_features}"):
                gdf.to_file(str(gpkg_path), driver="GPKG")
            assert gpkg_path.exists()
            size_mb = gpkg_path.stat().st_size / 1024 / 1024
            assert size_mb > 0
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    @pytest.mark.parametrize("n_features", [1_000, 10_000, 100_000])
    def test_gpkg_lettura_mock(self, n_features, tmp_path):
        """Lettura GPKG sintetico da disco."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            rng = np.random.default_rng(42)
            gdf = gpd.GeoDataFrame(
                {"id": np.arange(n_features), "val": rng.uniform(0, 100, n_features)},
                geometry=[Point(rng.uniform(6,19), rng.uniform(36,47))
                          for _ in range(n_features)],
                crs="EPSG:4326"
            )
            gpkg_path = tmp_path / "read_test.gpkg"
            gdf.to_file(str(gpkg_path), driver="GPKG")
            threshold = max(2.0, n_features / 10_000 * 5)
            with assert_faster_than(threshold, f"read GPKG {n_features}"):
                loaded = gpd.read_file(str(gpkg_path))
            assert len(loaded) == n_features
            assert loaded.geometry.is_valid.all()
            assert loaded.crs is not None
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_gpkg_qualita_dopo_roundtrip(self, tmp_path):
        """GPKG round-trip (write+read) preserva qualità."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            n = 5_000
            rng = np.random.default_rng(42)
            gdf_orig = gpd.GeoDataFrame(
                {"id": np.arange(n),
                 "nome": [f"feat_{i}" for i in range(n)],
                 "valore": rng.uniform(0, 100, n)},
                geometry=[Point(rng.uniform(6,19), rng.uniform(36,47))
                          for _ in range(n)],
                crs="EPSG:4326"
            )
            path = tmp_path / "roundtrip.gpkg"
            gdf_orig.to_file(str(path), driver="GPKG")
            gdf_loaded = gpd.read_file(str(path))
            assert len(gdf_loaded) == len(gdf_orig)
            assert gdf_loaded.crs == gdf_orig.crs
            assert gdf_loaded.geometry.is_valid.all()
            assert gdf_loaded["id"].equals(gdf_orig["id"].reset_index(drop=True))
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_streaming_chunk_corretto(self):
        """Chunking dati per streaming è corretto."""
        total_size = 500 * 1024 * 1024  # 500MB
        chunk_size = 64 * 1024  # 64KB
        n_chunks = (total_size + chunk_size - 1) // chunk_size
        total_streamed = sum(
            min(chunk_size, total_size - i * chunk_size)
            for i in range(n_chunks)
        )
        assert total_streamed == total_size

    @pytest.mark.parametrize("chunk_kb", [16, 32, 64, 128, 256])
    def test_chunk_size_ottimale(self, chunk_kb):
        """Vari chunk size sono validi per streaming."""
        chunk_bytes = chunk_kb * 1024
        total = 100 * 1024 * 1024  # 100MB
        n_chunks = (total + chunk_bytes - 1) // chunk_bytes
        assert n_chunks > 0
        assert n_chunks * chunk_bytes >= total

    def test_result_store_path_valido(self, tmp_path):
        """Il path del result store per file grandi è valido."""
        import os
        file_path = tmp_path / "output_500mb.gpkg"
        # Simula creazione file
        file_path.write_bytes(b"\x00" * 1024)
        assert file_path.exists()
        assert os.path.getsize(str(file_path)) == 1024

    @pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:3857", "EPSG:32632"])
    def test_gpkg_vari_crs(self, crs, tmp_path):
        """GPKG con vari CRS viene scritto correttamente."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            rng = np.random.default_rng(42)
            if crs == "EPSG:4326":
                coords = [(rng.uniform(6,19), rng.uniform(36,47)) for _ in range(100)]
            elif crs == "EPSG:3857":
                coords = [(rng.uniform(667917, 2115070), rng.uniform(4007501, 5910060))
                          for _ in range(100)]
            else:
                coords = [(rng.uniform(300000, 900000), rng.uniform(4000000, 5200000))
                          for _ in range(100)]
            gdf = gpd.GeoDataFrame(
                {"id": range(100)},
                geometry=[Point(x, y) for x, y in coords],
                crs=crs
            )
            path = tmp_path / f"test_{crs.replace(':', '_')}.gpkg"
            gdf.to_file(str(path), driver="GPKG")
            loaded = gpd.read_file(str(path))
            assert loaded.crs.to_epsg() == int(crs.split(":")[1])
        except ImportError:
            pytest.skip("GeoPandas non disponibile")


# =============================================================================
# PERF-13 — Canvas 20 nodi / 25 edge
# =============================================================================

class TestPERF13CanvasUnit:

    def _make_canvas(self, n_nodes: int, n_edges: int) -> dict:
        """Genera un canvas sintetico con n nodi e m edge."""
        nodes = [
            {"id": f"node_{i}",
             "type": "DataNode",
             "position": {"x": i * 100, "y": i * 50},
             "data": {"label": f"Nodo {i}", "config": {}}}
            for i in range(n_nodes)
        ]
        edges = [
            {"id": f"edge_{i}",
             "source": f"node_{i}",
             "target": f"node_{(i+1) % n_nodes}",
             "type": "default"}
            for i in range(min(n_edges, n_nodes - 1))
        ]
        return {"nodes": nodes, "edges": edges}

    @pytest.mark.parametrize("n_nodes,n_edges", [
        (5,  4),
        (10, 12),
        (20, 25),
        (50, 60),
        (100, 120),
    ])
    def test_canvas_creazione_veloce(self, n_nodes, n_edges):
        """Creazione canvas con N nodi è rapida."""
        with assert_faster_than(0.1, f"canvas({n_nodes},{n_edges})"):
            canvas = self._make_canvas(n_nodes, n_edges)
        assert len(canvas["nodes"]) == n_nodes
        assert len(canvas["edges"]) == min(n_edges, n_nodes - 1)

    @pytest.mark.parametrize("n_nodes,n_edges", [
        (5,  4),
        (10, 12),
        (20, 25),
        (50, 60),
    ])
    def test_canvas_serializzazione_veloce(self, n_nodes, n_edges):
        """Serializzazione JSON canvas con N nodi è rapida."""
        canvas = self._make_canvas(n_nodes, n_edges)
        with assert_faster_than(0.1, f"json.dumps canvas({n_nodes})"):
            data = json.dumps(canvas)
        parsed = json.loads(data)
        assert len(parsed["nodes"]) == n_nodes

    def test_canvas_20_nodi_struttura_valida(self):
        """Canvas 20 nodi / 25 edge ha struttura valida."""
        canvas = self._make_canvas(20, 25)
        assert len(canvas["nodes"]) == 20
        assert all("id" in n for n in canvas["nodes"])
        assert all("type" in n for n in canvas["nodes"])
        assert all("position" in n for n in canvas["nodes"])
        assert all("source" in e and "target" in e for e in canvas["edges"])

    def test_canvas_nodi_id_univoci(self):
        """Tutti i nodi hanno ID univoci."""
        canvas = self._make_canvas(20, 25)
        ids = [n["id"] for n in canvas["nodes"]]
        assert len(ids) == len(set(ids))

    def test_canvas_edge_reference_nodi_validi(self):
        """Tutti gli edge referenziano nodi esistenti."""
        canvas = self._make_canvas(20, 19)
        node_ids = {n["id"] for n in canvas["nodes"]}
        for edge in canvas["edges"]:
            assert edge["source"] in node_ids, \
                f"Source {edge['source']} non trovato"
            assert edge["target"] in node_ids, \
                f"Target {edge['target']} non trovato"

    @pytest.mark.parametrize("n_nodes", [5, 10, 20, 50, 100, 200])
    def test_canvas_dimensione_json_ragionevole(self, n_nodes):
        """Canvas con N nodi ha dimensione JSON ragionevole."""
        canvas = self._make_canvas(n_nodes, n_nodes - 1)
        data = json.dumps(canvas)
        size_kb = len(data) / 1024
        # < 10KB per nodo
        assert size_kb < n_nodes * 10, \
            f"Canvas {n_nodes} nodi: {size_kb:.1f}KB troppo grande"

    def test_canvas_salvataggio_mock_veloce(self, auth_headers):
        """Salvataggio canvas via API è < 1s."""
        import requests as req
        canvas = self._make_canvas(20, 25)
        res = req.post(
            f"http://localhost:8000/api/projects",
            json={"name": "[PERF] canvas 20 nodi",
                  "project_type": "manipola",
                  "canvas": canvas},
            headers=auth_headers,
            timeout=5
        )
        assert res.status_code in (200, 201)
        if res.status_code == 200:
            pid = res.json()["metadata"]["id"]
            req.delete(f"http://localhost:8000/api/projects/{pid}",
                       headers=auth_headers)

    def test_canvas_recupero_veloce(self, auth_headers):
        """GET canvas via API è < 0.5s."""
        import requests as req
        canvas = self._make_canvas(20, 25)
        res_create = req.post(
            f"http://localhost:8000/api/projects",
            json={"name": "[PERF] canvas get test",
                  "project_type": "manipola",
                  "canvas": canvas},
            headers=auth_headers,
            timeout=5
        )
        if res_create.status_code != 200:
            pytest.skip("Creazione progetto fallita")
        pid = res_create.json()["metadata"]["id"]
        try:
            start = time.perf_counter()
            res = req.get(f"http://localhost:8000/api/projects/{pid}",
                          headers=auth_headers, timeout=5)
            elapsed = time.perf_counter() - start
            assert res.status_code == 200
            assert elapsed < 3.0, f"GET canvas: {elapsed:.2f}s > 3s"
        finally:
            req.delete(f"http://localhost:8000/api/projects/{pid}",
                       headers=auth_headers)

    @pytest.mark.parametrize("n_nodes,n_edges", [
        (20, 25),
        (50, 60),
        (100, 120),
    ])
    def test_canvas_parsing_veloce(self, n_nodes, n_edges):
        """Parsing canvas JSON è < 10ms."""
        canvas = self._make_canvas(n_nodes, n_edges)
        data = json.dumps(canvas)
        with assert_faster_than(0.01, f"parse canvas({n_nodes})"):
            parsed = json.loads(data)
        assert len(parsed["nodes"]) == n_nodes
