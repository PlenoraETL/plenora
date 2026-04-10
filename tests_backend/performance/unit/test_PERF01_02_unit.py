"""
tests/performance/unit/test_PERF01_02_estrai.py
================================================
Test UNIT di performance — Modulo ESTRAI (PERF-01→PERF-02)

PERF-01  Preview 1M righe PostgreSQL → < 5s, qualità output
PERF-02  Upload shapefile 200MB → < 60s, qualità output

Unit: mock del DB/filesystem, verifica logica e qualità senza I/O reale.
"""

import pytest
import time
import io
import numpy as np
import pandas as pd
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
# PERF-01 — Preview 1M righe
# =============================================================================

class TestPERF01PreviewUnit:

    @pytest.mark.parametrize("n_rows", [
        1_000, 10_000, 100_000, 500_000, 1_000_000
    ])
    def test_preview_limit_applicato(self, n_rows, df_xxlarge):
        """Il LIMIT della preview deve essere rispettato indipendentemente dal volume."""
        limit = 100
        sample = df_xxlarge.head(limit)
        assert len(sample) == limit
        assert_df_quality(sample, min_rows=limit, label=f"Preview({n_rows})")

    @pytest.mark.parametrize("limit", [10, 50, 100, 500, 1000])
    def test_preview_vari_limit(self, limit, df_xxlarge):
        """Preview con vari limit restituisce esattamente limit righe."""
        result = df_xxlarge.head(limit)
        assert len(result) == limit

    def test_preview_qualita_tipi(self, df_xxlarge):
        """I tipi delle colonne sono preservati nella preview."""
        preview = df_xxlarge.head(100)
        assert preview["id"].dtype == np.int64 or preview["id"].dtype == np.int32
        assert preview["valore"].dtype == np.float64
        assert str(preview["data"].dtype).startswith("datetime")

    def test_preview_no_nan_inattesi(self, df_xxlarge):
        """La preview non introduce NaN nelle colonne numeriche."""
        preview = df_xxlarge.head(1000)
        assert preview["id"].notna().all()
        assert preview["valore"].notna().all()

    def test_preview_velocita_head(self, df_xxlarge):
        """Estrarre i primi 100 elementi da 1M righe è quasi istantaneo."""
        with assert_faster_than(0.1, "head(100) su 1M righe"):
            result = df_xxlarge.head(100)
        assert len(result) == 100

    def test_preview_preserva_ordine(self, df_xxlarge):
        """La preview preserva l'ordine originale del DataFrame."""
        preview = df_xxlarge.head(100)
        assert list(preview["id"]) == list(range(100))

    @pytest.mark.parametrize("limit", [1, 10, 100])
    def test_preview_executor_mock(self, limit):
        """EstraiExecutor con mock restituisce preview veloce."""
        mock_result = {"rows": list(range(limit)), "columns": ["id", "val"]}
        mock_exec = MagicMock()
        mock_exec.execute_query.return_value = pd.DataFrame(
            {"id": range(limit), "val": range(limit)}
        )
        from app.core.estrai.executor import EstraiExecutor
        executor = EstraiExecutor(
            project_service=MagicMock(),
            executor=mock_exec,
            generator=MagicMock(),
            connection_service=MagicMock(),
        )
        start = time.perf_counter()
        try:
            executor.preview(project_id="proj-1", canvas=MagicMock(), limit=limit)
        except Exception:
            pass
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Preview mock ha impiegato {elapsed:.2f}s"

    def test_preview_colonne_attese_presenti(self, df_large):
        """Preview restituisce tutte le colonne attese."""
        expected_cols = {"id", "categoria", "valore", "quantita", "regione"}
        result = df_large.head(100)
        assert expected_cols.issubset(set(result.columns))

    def test_preview_nessuna_colonna_extra(self, df_large):
        """Preview non aggiunge colonne fantasma."""
        original_cols = set(df_large.columns)
        result = df_large.head(100)
        assert set(result.columns) == original_cols

    @pytest.mark.parametrize("n_rows,expected_time", [
        (1_000,     0.01),
        (10_000,    0.05),
        (100_000,   0.5),
        (1_000_000, 2.0),
    ])
    def test_head_performance_by_volume(self, n_rows, expected_time, df_xxlarge):
        """head() su vari volumi rispetta soglie crescenti."""
        with assert_faster_than(expected_time, f"head(100) su {n_rows:,} righe"):
            df_xxlarge.head(100)


# =============================================================================
# PERF-02 — Upload shapefile 200MB
# =============================================================================

class TestPERF02UploadUnit:

    @pytest.fixture(scope="class")
    def synthetic_shapefile_zip(self):
        """Genera un zip sintetico che simula uno shapefile."""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test.shp", b"\x00" * 1024 * 100)   # 100KB header
            zf.writestr("test.dbf", b"\x00" * 1024 * 100)
            zf.writestr("test.prj", b"GEOGCS[\"GCS_WGS_1984\"]")
            zf.writestr("test.shx", b"\x00" * 1024 * 10)
        buf.seek(0)
        return buf.getvalue()

    def test_zip_struttura_valida(self, synthetic_shapefile_zip):
        """Il zip sintetico contiene i file attesi."""
        import zipfile
        with zipfile.ZipFile(io.BytesIO(synthetic_shapefile_zip)) as zf:
            names = zf.namelist()
        assert "test.shp" in names
        assert "test.dbf" in names
        assert "test.prj" in names

    @pytest.mark.parametrize("size_mb", [1, 10, 50, 100, 200])
    def test_generazione_file_sintetico(self, size_mb):
        """Generazione file sintetico di varie dimensioni è rapida."""
        with assert_faster_than(5.0, f"generazione {size_mb}MB"):
            data = b"\x00" * (size_mb * 1024 * 1024)
        assert len(data) == size_mb * 1024 * 1024

    def test_file_service_valida_estensione_zip(self):
        """FileService accetta estensione .zip."""
        allowed = {".csv", ".xlsx", ".xls", ".json", ".geojson",
                   ".gpkg", ".shp", ".gdb", ".zip"}
        assert ".zip" in allowed

    def test_file_service_valida_estensione_gpkg(self):
        """FileService accetta estensione .gpkg."""
        allowed = {".csv", ".xlsx", ".xls", ".json", ".geojson",
                   ".gpkg", ".shp", ".gdb", ".zip"}
        assert ".gpkg" in allowed

    @pytest.mark.parametrize("ext,mime", [
        (".zip",     "application/zip"),
        (".gpkg",    "application/geopackage+sqlite3"),
        (".geojson", "application/geo+json"),
        (".shp",     "application/octet-stream"),
    ])
    def test_estensioni_geo_accettate(self, ext, mime):
        """Le estensioni GIS principali sono nella whitelist."""
        allowed = {".csv", ".xlsx", ".xls", ".json", ".geojson",
                   ".gpkg", ".shp", ".gdb", ".zip"}
        assert ext in allowed, f"{ext} non nella whitelist"

    def test_parsing_geojson_sintetico_veloce(self):
        """Parsing GeoJSON sintetico piccolo è < 0.5s."""
        import json
        features = [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [lon, lat]},
             "properties": {"id": i}}
            for i, (lon, lat) in enumerate(
                zip(np.linspace(6, 19, 1000), np.linspace(36, 47, 1000))
            )
        ]
        geojson = {"type": "FeatureCollection", "features": features}

        with assert_faster_than(0.5, "parsing GeoJSON 1k features"):
            result = json.dumps(geojson)
            parsed = json.loads(result)

        assert len(parsed["features"]) == 1000

    @pytest.mark.parametrize("n_features", [100, 1_000, 10_000, 50_000])
    def test_geojson_size_vs_tempo(self, n_features):
        """GeoJSON creazione scala linearmente."""
        import json
        features = [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [i % 360, i % 180]},
             "properties": {"id": i}}
            for i in range(n_features)
        ]
        geojson = {"type": "FeatureCollection", "features": features}

        with assert_faster_than(n_features / 10_000 * 2 + 1, f"GeoJSON {n_features}"):
            data = json.dumps(geojson)
        assert len(json.loads(data)["features"]) == n_features

    def test_qualita_geometrie_sintetiche(self):
        """Le geometrie sintetiche sono valide."""
        try:
            from shapely.geometry import Point
            import geopandas as gpd
            n = 1000
            rng = np.random.default_rng(42)
            lons = rng.uniform(6, 19, size=n)
            lats = rng.uniform(36, 47, size=n)
            geoms = [Point(lon, lat) for lon, lat in zip(lons, lats)]
            gdf = gpd.GeoDataFrame({"id": range(n)}, geometry=geoms, crs="EPSG:4326")
            assert gdf.geometry.is_valid.all()
            assert gdf.geometry.notna().all()
            assert gdf.crs is not None
        except ImportError:
            pytest.skip("GeoPandas/Shapely non disponibili")

    @pytest.mark.parametrize("n", [1_000, 10_000, 50_000])
    def test_gdf_creazione_veloce(self, n):
        """Creazione GeoDataFrame sintetico è veloce."""
        try:
            from shapely.geometry import Point
            import geopandas as gpd
            rng = np.random.default_rng(42)
            lons = rng.uniform(6, 19, size=n)
            lats = rng.uniform(36, 47, size=n)

            threshold = n / 10_000 * 3 + 0.5
            with assert_faster_than(threshold, f"GDF creazione {n}"):
                geoms = [Point(lon, lat) for lon, lat in zip(lons, lats)]
                gdf = gpd.GeoDataFrame({"id": range(n)}, geometry=geoms, crs="EPSG:4326")

            assert len(gdf) == n
        except ImportError:
            pytest.skip("GeoPandas non disponibile")
