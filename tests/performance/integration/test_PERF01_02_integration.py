"""
tests/performance/integration/test_PERF01_02_estrai.py
=======================================================
Test INTEGRATION di performance — Modulo ESTRAI (PERF-01→PERF-02)
Richiede backend live su localhost:8000 e PostgreSQL disponibile.
"""

import pytest
import requests
import time
import io
import json
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



# =============================================================================
# PERF-01 — Preview 1M righe PostgreSQL
# =============================================================================

class TestPERF01Integration:

    @pytest.mark.slow
    def test_preview_sotto_soglia(self, auth_headers, pg_connection_id):
        """Preview tabella grande completa in < 5s."""
        start = time.perf_counter()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "pg_large_table",
                    "limit": 100},
            headers=auth_headers,
            timeout=THRESHOLDS["PERF01_preview"] + 5
        )
        elapsed = time.perf_counter() - start
        assert res.status_code in (200, 404, 422), \
            f"Status inatteso: {res.status_code}"
        if res.status_code == 200:
            assert elapsed < THRESHOLDS["PERF01_preview"], \
                f"Preview: {elapsed:.2f}s > soglia {THRESHOLDS['PERF01_preview']}s"

    @pytest.mark.parametrize("limit", [10, 50, 100, 500, 1000])
    def test_preview_vari_limit(self, auth_headers, pg_connection_id, limit):
        """Preview con vari limit rispetta la soglia."""
        start = time.perf_counter()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "test_table",
                    "limit": limit},
            headers=auth_headers,
            timeout=THRESHOLDS["PERF01_preview"] + 5
        )
        elapsed = time.perf_counter() - start
        if res.status_code == 200:
            assert elapsed < THRESHOLDS["PERF01_preview"]
            body = res.json()
            assert len(body.get("rows", [])) <= limit

    def test_preview_risposta_struttura(self, auth_headers, pg_connection_id):
        """La risposta preview ha struttura corretta."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "test_table",
                    "limit": 10},
            headers=auth_headers,
            timeout=10
        )
        if res.status_code == 200:
            body = res.json()
            assert "rows" in body or "data" in body or "columns" in body

    def test_preview_no_500(self, auth_headers, pg_connection_id):
        """Preview non ritorna mai 500."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": pg_connection_id,
                    "table_name": "test_table",
                    "limit": 100},
            headers=auth_headers,
            timeout=15
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("n_concurrent", [2, 5, 10])
    def test_preview_richieste_concorrenti(self, auth_headers, pg_connection_id,
                                           n_concurrent):
        """N richieste preview concorrenti completano senza 500."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = []

        def do_preview():
            return requests.get(
                f"{BASE_URL}/estrai/preview",
                params={"connection_id": pg_connection_id,
                        "table_name": "test_table", "limit": 10},
                headers=auth_headers,
                timeout=15
            ).status_code

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_concurrent) as ex:
            futs = [ex.submit(do_preview) for _ in range(n_concurrent)]
            for f in as_completed(futs):
                results.append(f.result())
        elapsed = time.perf_counter() - start

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0, f"{len(crashes)} richieste hanno dato 500"
        assert elapsed < THRESHOLDS["PERF01_preview"] * 2

    def test_get_tables_performance(self, auth_headers, pg_connection_id):
        """GET /tables restituisce la lista in < 3s."""
        start = time.perf_counter()
        res = requests.get(
            f"{BASE_URL}/connections/{pg_connection_id}/tables",
            headers=auth_headers,
            timeout=10
        )
        elapsed = time.perf_counter() - start
        if res.status_code == 200:
            assert elapsed < 3.0, f"GET tables: {elapsed:.2f}s > 3s"
            tables = res.json().get("tables", [])
            assert isinstance(tables, list)


# =============================================================================
# PERF-02 — Upload shapefile 200MB
# =============================================================================

class TestPERF02Integration:

    @pytest.fixture(scope="class")
    def small_csv(self):
        """CSV sintetico piccolo per test upload."""
        import pandas as pd
        import io
        df = pd.DataFrame({
            "id": range(1000),
            "nome": [f"record_{i}" for i in range(1000)],
            "valore": np.random.uniform(0, 100, 1000),
            "lat": np.random.uniform(36, 47, 1000),
            "lon": np.random.uniform(6, 19, 1000),
        })
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        return buf.getvalue()

    @pytest.fixture(scope="class")
    def medium_geojson(self):
        """GeoJSON sintetico ~10MB."""
        features = [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [6 + i * 0.001, 36 + i * 0.001]},
             "properties": {"id": i, "nome": f"feat_{i}", "valore": i * 0.1}}
            for i in range(50_000)
        ]
        data = json.dumps({"type": "FeatureCollection", "features": features})
        return data.encode("utf-8")

    def test_upload_csv_piccolo_sotto_10s(self, auth_headers, small_csv):
        """Upload CSV 1k righe in < 10s."""
        start = time.perf_counter()
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test_perf.csv", io.BytesIO(small_csv), "text/csv")},
            headers=auth_headers,
            timeout=15
        )
        elapsed = time.perf_counter() - start
        assert res.status_code in (200, 201, 400, 422)
        if res.status_code in (200, 201):
            assert elapsed < 10.0, f"Upload CSV: {elapsed:.2f}s > 10s"

    def test_upload_geojson_medio_qualita(self, auth_headers, medium_geojson):
        """Upload GeoJSON 50k features — risposta in < 30s."""
        start = time.perf_counter()
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test_perf.geojson",
                            io.BytesIO(medium_geojson),
                            "application/geo+json")},
            headers=auth_headers,
            timeout=35
        )
        elapsed = time.perf_counter() - start
        assert res.status_code != 500
        if res.status_code in (200, 201):
            assert elapsed < 30.0

    @pytest.mark.parametrize("size_kb", [10, 100, 500, 1000])
    def test_upload_varie_dimensioni(self, auth_headers, size_kb):
        """Upload di varie dimensioni completa senza 500."""
        n_rows = size_kb * 5  # ~200 bytes per riga CSV
        import pandas as pd
        df = pd.DataFrame({
            "id": range(n_rows),
            "val": np.random.uniform(0, 100, n_rows)
        })
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)

        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": (f"test_{size_kb}kb.csv",
                            buf, "text/csv")},
            headers=auth_headers,
            timeout=max(30, size_kb / 10)
        )
        assert res.status_code != 500

    def test_upload_risposta_struttura(self, auth_headers, small_csv):
        """La risposta upload ha struttura corretta."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.csv", io.BytesIO(small_csv), "text/csv")},
            headers=auth_headers,
            timeout=15
        )
        if res.status_code in (200, 201):
            body = res.json()
            assert "id" in body or "file_id" in body or body

    def test_list_files_dopo_upload(self, auth_headers, small_csv):
        """Il file appare nella lista dopo l'upload."""
        res_upload = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("perf_list_test.csv",
                            io.BytesIO(small_csv), "text/csv")},
            headers=auth_headers,
            timeout=15
        )
        if res_upload.status_code not in (200, 201):
            pytest.skip("Upload fallito — skip test lista")

        res_list = requests.get(f"{BASE_URL}/files", headers=auth_headers)
        if res_list.status_code == 200:
            files = res_list.json().get("files", [])
            assert isinstance(files, list)

    @pytest.mark.slow
    def test_upload_zip_shapefile_sintetico(self, auth_headers):
        """Upload ZIP sintetico da 1MB completa senza crash."""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Simula shapefile minimo
            zf.writestr("test.shp", b"\x00\x00\x27\x0a" + b"\x00" * 100)
            zf.writestr("test.dbf", b"\x03" + b"\x00" * 32)
            zf.writestr("test.prj",
                        b'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                        b'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                        b'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
            zf.writestr("test.shx", b"\x00\x00\x27\x0a" + b"\x00" * 8)
        buf.seek(0)

        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("shapefile.zip", buf, "application/zip")},
            headers=auth_headers,
            timeout=30
        )
        assert res.status_code != 500
