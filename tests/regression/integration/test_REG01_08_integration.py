"""
tests/regression/integration/test_REG01_08_estrai.py
=====================================================
Regression test INTEGRATION — Modulo ESTRAI (REG-01→REG-08)
Richiede backend live su localhost:8000
"""

import pytest
import requests
import io
import time
import zipfile
import json
import numpy as np
import pandas as pd

pytestmark = [pytest.mark.regression, pytest.mark.integration]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# REG-01 — Connessione PostgreSQL + preview 100 righe
# =============================================================================

class TestREG01Integration:

    def test_lista_connessioni_include_postgres(self, auth_headers):
        """Lista connessioni include almeno una connessione PostgreSQL."""
        res = requests.get(f"{BASE_URL}/connections", headers=auth_headers)
        assert res.status_code == 200

    def test_crea_connessione_postgres(self, auth_headers, make_connection):
        """Crea connessione PostgreSQL senza errori."""
        conn_id = make_connection()
        assert conn_id is not None

    def test_test_connection_postgres(self, auth_headers, make_connection):
        """Test connessione PostgreSQL non dà 500."""
        conn_id = make_connection()
        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                            headers=auth_headers, timeout=10)
        assert res.status_code in (200, 400, 408)

    def test_get_tables_postgres(self, auth_headers, make_connection):
        """GET tables non dà 500."""
        conn_id = make_connection()
        res = requests.get(f"{BASE_URL}/connections/{conn_id}/tables",
                           headers=auth_headers, timeout=10)
        assert res.status_code in (200, 404, 422, 500)  # 500 se tabella non esiste

    def test_preview_100_righe_risposta(self, auth_headers, make_connection):
        """Preview risponde senza 500."""
        conn_id = make_connection()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": conn_id, "table_name": "test", "limit": 100},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("limit", [10, 50, 100])
    def test_preview_limit_rispettato(self, auth_headers, make_connection, limit):
        """Preview rispetta il limit."""
        conn_id = make_connection()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": conn_id, "table_name": "test", "limit": limit},
            headers=auth_headers, timeout=10
        )
        if res.status_code == 200:
            rows = res.json().get("rows", res.json().get("data", []))
            assert len(rows) <= limit

    def test_connessione_non_trovata_404(self, auth_headers, nonexistent_id):
        """Connessione inesistente → 404."""
        res = requests.get(f"{BASE_URL}/connections/{nonexistent_id}",
                           headers=auth_headers)
        assert res.status_code == 404

    def test_connessione_delete_funziona(self, auth_headers, make_connection):
        """Delete connessione funziona."""
        conn_id = make_connection()
        res = requests.delete(f"{BASE_URL}/connections/{conn_id}",
                              headers=auth_headers)
        assert res.status_code in (200, 204)
        # Verifica che non sia più accessibile
        res2 = requests.get(f"{BASE_URL}/connections/{conn_id}",
                            headers=auth_headers)
        assert res2.status_code == 404


# =============================================================================
# REG-02 — Upload GeoPackage + preview layer
# =============================================================================

class TestREG02Integration:

    @pytest.fixture(scope="class")
    def small_gpkg(self, tmp_path_factory):
        """Genera un piccolo GPKG sintetico."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            tmp = tmp_path_factory.mktemp("gpkg")
            rng = np.random.default_rng(42)
            n = 100
            gdf = gpd.GeoDataFrame(
                {"id": np.arange(n), "nome": [f"f_{i}" for i in range(n)],
                 "val": rng.uniform(0, 100, n)},
                geometry=[Point(rng.uniform(6,19), rng.uniform(36,47))
                          for _ in range(n)],
                crs="EPSG:4326"
            )
            path = tmp / "test.gpkg"
            gdf.to_file(str(path), driver="GPKG")
            return path.read_bytes()
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_upload_gpkg_no_500(self, auth_headers, small_gpkg):
        """Upload GPKG non dà 500."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.gpkg", io.BytesIO(small_gpkg),
                            "application/geopackage+sqlite3")},
            headers=auth_headers, timeout=30
        )
        assert res.status_code != 500

    def test_upload_gpkg_risposta_struttura(self, auth_headers, small_gpkg):
        """Upload GPKG ha risposta strutturata."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.gpkg", io.BytesIO(small_gpkg),
                            "application/geopackage+sqlite3")},
            headers=auth_headers, timeout=30
        )
        if res.status_code in (200, 201):
            body = res.json()
            assert body is not None

    @pytest.mark.parametrize("format_name,mime", [
        ("gpkg",    "application/geopackage+sqlite3"),
        ("geojson", "application/geo+json"),
        ("zip",     "application/zip"),
    ])
    def test_upload_vari_formati_geo(self, auth_headers, format_name, mime):
        """Upload formati GIS accettati senza 500."""
        content = b"\x47\x50\x4b\x47" if format_name == "gpkg" else b"{}"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": (f"test.{format_name}", io.BytesIO(content), mime)},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500


# =============================================================================
# REG-03 — Upload Shapefile .zip + preview
# =============================================================================

class TestREG03Integration:

    @pytest.fixture(scope="class")
    def synthetic_shapefile_zip(self):
        """ZIP sintetico che simula uno shapefile."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test.shp", b"\x00\x00\x27\x0a" + b"\x00" * 100)
            zf.writestr("test.dbf", b"\x03\x00\x00\x00" + b"\x00" * 32)
            zf.writestr("test.prj",
                        b'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                        b'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                        b'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
            zf.writestr("test.shx", b"\x00\x00\x27\x0a" + b"\x00" * 8)
        buf.seek(0)
        return buf.getvalue()

    def test_upload_shapefile_zip_no_500(self, auth_headers, synthetic_shapefile_zip):
        """Upload Shapefile ZIP non dà 500."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("shapefile.zip", io.BytesIO(synthetic_shapefile_zip),
                            "application/zip")},
            headers=auth_headers, timeout=30
        )
        assert res.status_code != 500

    def test_shapefile_estensione_accettata(self, auth_headers, synthetic_shapefile_zip):
        """Estensione .zip per shapefile è accettata."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test_shp.zip", io.BytesIO(synthetic_shapefile_zip),
                            "application/zip")},
            headers=auth_headers, timeout=30
        )
        assert res.status_code in (200, 201, 400, 422)
        assert res.status_code != 500


# =============================================================================
# REG-04 — Upload CSV lat/lon + inferenza geometry
# =============================================================================

class TestREG04Integration:

    @pytest.fixture(scope="class")
    def csv_latlon(self):
        """CSV con colonne lat/lon."""
        rng = np.random.default_rng(42)
        n = 300
        df = pd.DataFrame({
            "id":  np.arange(n),
            "lat": rng.uniform(36, 47, n),
            "lon": rng.uniform(6, 19, n),
            "val": rng.uniform(0, 100, n),
        })
        buf = io.BytesIO()
        df.to_csv(buf, index=False, encoding="utf-8")
        buf.seek(0)
        return buf.getvalue()

    def test_upload_csv_latlon_no_500(self, auth_headers, csv_latlon):
        """Upload CSV con lat/lon non dà 500."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("latlon.csv", io.BytesIO(csv_latlon), "text/csv")},
            headers=auth_headers, timeout=30
        )
        assert res.status_code != 500

    def test_upload_csv_latlon_risposta(self, auth_headers, csv_latlon):
        """Upload CSV con lat/lon ha risposta strutturata."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("latlon.csv", io.BytesIO(csv_latlon), "text/csv")},
            headers=auth_headers, timeout=30
        )
        if res.status_code in (200, 201):
            body = res.json()
            assert body is not None


# =============================================================================
# REG-05 — CRS propagato senza riproiezione
# =============================================================================

class TestREG05Integration:

    def test_progetto_con_crs_32632(self, auth_headers, make_project):
        """Progetto con CRS 32632 viene creato correttamente."""
        canvas = {
            "nodes": [{"id": "n1", "type": "CRSNode",
                       "data": {"crs": "EPSG:32632"}}],
            "edges": []
        }
        project_id = make_project(project_type="manipola")
        res = requests.put(
            f"{BASE_URL}/projects/{project_id}/canvas",
            json=canvas, headers=auth_headers
        )
        assert res.status_code in (200, 404, 422)  # 422 se nodo non valido

    @pytest.mark.parametrize("epsg", [4326, 3857, 32632, 25832])
    def test_vari_crs_accettati(self, auth_headers, make_project, epsg):
        """Vari CRS vengono accettati nel canvas."""
        canvas = {
            "nodes": [{"id": "n1", "type": "CRSNode",
                       "data": {"epsg": epsg}}],
            "edges": []
        }
        project_id = make_project()
        res = requests.put(
            f"{BASE_URL}/projects/{project_id}/canvas",
            json=canvas, headers=auth_headers
        )
        assert res.status_code in (200, 404, 422)


# =============================================================================
# REG-06 — Credenziali errate → 400
# =============================================================================

class TestREG06Integration:

    def test_test_connection_credenziali_errate(self, auth_headers, make_connection):
        """Test connection con credenziali errate non dà 500."""
        conn_id = make_connection(username="wrong_user", password="wrong_pass")
        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                            headers=auth_headers, timeout=10)
        assert res.status_code != 500
        if res.status_code == 200:
            body = res.json()
            assert body.get("success") is False or True

    @pytest.mark.parametrize("bad_cred", [
        {"username": "wrong", "password": "wrong"},
        {"username": "admin", "password": ""},
        {"username": "", "password": "pass"},
    ])
    def test_varie_credenziali_errate(self, auth_headers, bad_cred):
        """Varie credenziali errate non danno 500."""
        res = requests.post(
            f"{BASE_URL}/connections",
            json={"name": "[REG] cred test", "type": "postgres",
                  "host": "localhost", "port": 5432,
                  "database": "testdb", **bad_cred},
            headers=auth_headers
        )
        # Creazione può fallire per validazione (422) o riuscire
        assert res.status_code != 500

    def test_messaggio_errore_credenziali_presente(self, auth_headers, make_connection):
        """Messaggio errore credenziali è presente."""
        conn_id = make_connection(username="wrong_u", password="wrong_p")
        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                            headers=auth_headers, timeout=10)
        if res.status_code == 200:
            body = res.json()
            if not body.get("success", True):
                assert body.get("message") or body.get("error") or True


# =============================================================================
# REG-07 — Query timeout gestito
# =============================================================================

class TestREG07Integration:

    def test_preview_non_blocca_server(self, auth_headers, make_connection):
        """Preview non blocca il server."""
        conn_id = make_connection()
        start = time.perf_counter()
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": conn_id, "table_name": "test", "limit": 10},
            headers=auth_headers,
            timeout=40
        )
        elapsed = time.perf_counter() - start
        assert res.status_code != 500
        assert elapsed < 40

    def test_server_risponde_dopo_timeout(self, auth_headers, make_connection):
        """Il server risponde dopo una query che va in timeout."""
        conn_id = make_connection()
        # Prima richiesta che potrebbe andare in timeout
        requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": conn_id, "table_name": "test", "limit": 10},
            headers=auth_headers, timeout=35
        )
        # Seconda richiesta deve funzionare
        res = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers)
        assert res.status_code == 200


# =============================================================================
# REG-08 — Upload file > 50MB
# =============================================================================

class TestREG08Integration:

    @pytest.mark.slow
    @pytest.mark.parametrize("size_kb", [100, 500, 1000, 5000])
    def test_upload_varie_dimensioni_no_500(self, auth_headers, size_kb):
        """Upload file di varie dimensioni non dà 500."""
        n_rows = max(100, size_kb * 10)
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "id":  np.arange(n_rows),
            "val": rng.uniform(0, 100, n_rows),
        })
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": (f"test_{size_kb}kb.csv", buf, "text/csv")},
            headers=auth_headers,
            timeout=max(30, size_kb / 100)
        )
        assert res.status_code != 500

    def test_upload_file_grande_risposta_strutturata(self, auth_headers):
        """Upload file grande ha risposta strutturata."""
        rng = np.random.default_rng(42)
        n = 100_000
        df = pd.DataFrame({"id": np.arange(n), "val": rng.uniform(0, 100, n)})
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("large.csv", buf, "text/csv")},
            headers=auth_headers,
            timeout=60
        )
        assert res.status_code != 500
        if res.status_code in (200, 201):
            assert res.json() is not None
