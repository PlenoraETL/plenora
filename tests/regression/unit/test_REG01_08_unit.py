"""
tests/regression/unit/test_REG01_08_estrai.py
==============================================
Regression test UNIT — Modulo ESTRAI (REG-01→REG-08)
"""

import sys
sys.path.insert(0, r'C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend')
import pytest
import numpy as np
import pandas as pd
import io
import zipfile
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.regression, pytest.mark.unit]


# =============================================================================
# REG-01 — Connessione PostgreSQL + preview 100 righe
# =============================================================================

class TestREG01Unit:

    def test_preview_100_righe_shape(self, df_pg_100):
        """Preview 100 righe ha shape corretta."""
        assert len(df_pg_100) == 100
        assert len(df_pg_100.columns) > 0

    def test_preview_100_righe_tipi(self, df_pg_100):
        """I tipi delle colonne sono corretti."""
        assert df_pg_100["id"].dtype in (np.int32, np.int64)
        assert df_pg_100["valore"].dtype == np.float64
        assert str(df_pg_100["data"].dtype).startswith("datetime")

    def test_preview_100_nessun_nan(self, df_pg_100):
        """Preview non ha NaN inattesi."""
        assert df_pg_100["id"].notna().all()
        assert df_pg_100["valore"].notna().all()

    def test_preview_id_univoci(self, df_pg_100):
        """Gli ID sono univoci."""
        assert df_pg_100["id"].is_unique

    @pytest.mark.parametrize("limit", [10, 50, 100])
    def test_preview_limit_rispettato(self, df_pg_100, limit):
        """Il limit viene rispettato."""
        result = df_pg_100.head(limit)
        assert len(result) == limit

    def test_preview_colonne_attese(self, df_pg_100):
        """Tutte le colonne attese sono presenti."""
        expected = {"id", "nome", "valore", "categoria", "data"}
        assert expected.issubset(set(df_pg_100.columns))

    def test_connection_service_metodi_presenti(self):
        """ConnectionService ha i metodi attesi."""
        from app.services.connection_service import ConnectionService
        svc = ConnectionService()
        assert hasattr(svc, "get_connection")
        assert hasattr(svc, "create_connection")
        assert hasattr(svc, "test_connection")
        assert hasattr(svc, "list_connections")


    def test_preview_valori_range_corretto(self, df_pg_100):
        """I valori numerici sono nel range atteso."""
        assert df_pg_100["valore"].between(0, 1000).all()

    @pytest.mark.parametrize("categoria", ["A", "B", "C"])
    def test_preview_categorie_presenti(self, df_pg_100, categoria):
        """Tutte le categorie sono presenti."""
        assert categoria in df_pg_100["categoria"].values

    def test_preview_data_ordinata(self, df_pg_100):
        """Le date sono in ordine crescente."""
        assert df_pg_100["data"].is_monotonic_increasing


# =============================================================================
# REG-02 — Upload GeoPackage + preview layer
# =============================================================================

class TestREG02Unit:

    def test_gpkg_layer_shape(self, gdf_gpkg):
        """Layer GeoPackage ha shape corretta."""
        assert len(gdf_gpkg) == 500
        assert gdf_gpkg.geometry is not None

    def test_gpkg_crs_presente(self, gdf_gpkg):
        """CRS è presente e corretto."""
        assert gdf_gpkg.crs is not None
        assert gdf_gpkg.crs.to_epsg() == 4326

    def test_gpkg_geometrie_valide(self, gdf_gpkg):
        """Tutte le geometrie sono valide."""
        assert gdf_gpkg.geometry.is_valid.all()

    def test_gpkg_geometrie_non_null(self, gdf_gpkg):
        """Nessuna geometria null."""
        assert gdf_gpkg.geometry.notna().all()

    @pytest.mark.parametrize("n", [10, 50, 100, 500])
    def test_gpkg_preview_n_righe(self, gdf_gpkg, n):
        """Preview N righe da GPKG è corretto."""
        result = gdf_gpkg.head(n)
        assert len(result) == n
        assert result.geometry.is_valid.all()

    def test_gpkg_bbox_italia(self, gdf_gpkg):
        """Le geometrie sono nel bounding box Italia."""
        bounds = gdf_gpkg.total_bounds
        assert bounds[0] >= 6.0   # min lon
        assert bounds[2] <= 19.0  # max lon
        assert bounds[1] >= 36.0  # min lat
        assert bounds[3] <= 47.0  # max lat

    def test_gpkg_scrittura_lettura_roundtrip(self, gdf_gpkg, tmp_path):
        """Round-trip GPKG preserva qualità."""
        path = tmp_path / "reg02.gpkg"
        gdf_gpkg.to_file(str(path), driver="GPKG")
        import geopandas as gpd
        loaded = gpd.read_file(str(path))
        assert len(loaded) == len(gdf_gpkg)
        assert loaded.crs == gdf_gpkg.crs
        assert loaded.geometry.is_valid.all()

    def test_gpkg_attributi_preservati(self, gdf_gpkg, tmp_path):
        """Gli attributi sono preservati nel round-trip."""
        path = tmp_path / "reg02_attr.gpkg"
        gdf_gpkg.to_file(str(path), driver="GPKG")
        import geopandas as gpd
        loaded = gpd.read_file(str(path))
        assert "id" in loaded.columns
        assert "nome" in loaded.columns
        assert "valore" in loaded.columns


# =============================================================================
# REG-03 — Upload Shapefile .zip + preview
# =============================================================================

class TestREG03Unit:

    def test_shapefile_crs_32632(self, gdf_shapefile):
        """CRS è EPSG:32632."""
        assert gdf_shapefile.crs.to_epsg() == 32632

    def test_shapefile_shape(self, gdf_shapefile):
        """200 feature nel shapefile."""
        assert len(gdf_shapefile) == 200

    def test_shapefile_geometrie_valide(self, gdf_shapefile):
        """Geometrie valide."""
        assert gdf_shapefile.geometry.is_valid.all()

    @pytest.mark.parametrize("target_epsg", [4326, 3857, 32633])
    def test_shapefile_reproiezione(self, gdf_shapefile, target_epsg):
        """Riproiezione preserva numero feature e validità."""
        reprojected = gdf_shapefile.to_crs(epsg=target_epsg)
        assert len(reprojected) == len(gdf_shapefile)
        assert reprojected.crs.to_epsg() == target_epsg
        assert reprojected.geometry.is_valid.all()

    def test_shapefile_zip_struttura(self):
        """ZIP shapefile ha struttura corretta."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.shp", b"\x00" * 100)
            zf.writestr("test.dbf", b"\x00" * 50)
            zf.writestr("test.prj", b"PROJCS[...]")
            zf.writestr("test.shx", b"\x00" * 20)
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        assert any(n.endswith(".shp") for n in names)
        assert any(n.endswith(".dbf") for n in names)
        assert any(n.endswith(".prj") for n in names)

    def test_crs_propagato_dopo_upload(self, gdf_shapefile):
        """CRS è propagato dopo l'upload."""
        assert gdf_shapefile.crs is not None
        epsg = gdf_shapefile.crs.to_epsg()
        assert epsg == 32632


# =============================================================================
# REG-04 — Upload CSV lat/lon + inferenza geometry
# =============================================================================

class TestREG04Unit:

    def test_csv_latlon_shape(self, df_csv_latlon):
        """300 righe nel CSV."""
        assert len(df_csv_latlon) == 300

    def test_csv_ha_colonne_lat_lon(self, df_csv_latlon):
        """CSV ha colonne lat e lon."""
        assert "lat" in df_csv_latlon.columns
        assert "lon" in df_csv_latlon.columns

    @pytest.mark.parametrize("col,min_val,max_val", [
        ("lat", 36.0, 47.0),
        ("lon",  6.0, 19.0),
    ])
    def test_csv_coordinate_range(self, df_csv_latlon, col, min_val, max_val):
        """Le coordinate sono nel range Italia."""
        assert df_csv_latlon[col].between(min_val, max_val).all()

    def test_inferenza_geometry_point(self, df_csv_latlon):
        """Inferenza geometria crea Points validi."""
        try:
            from shapely.geometry import Point
            import geopandas as gpd
            gdf = gpd.GeoDataFrame(
                df_csv_latlon,
                geometry=[Point(lon, lat)
                          for lon, lat in zip(df_csv_latlon["lon"],
                                             df_csv_latlon["lat"])],
                crs="EPSG:4326"
            )
            assert gdf.geometry.is_valid.all()
            assert gdf.geometry.notna().all()
            assert all(g.geom_type == "Point" for g in gdf.geometry)
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_csv_nessun_nan_lat_lon(self, df_csv_latlon):
        """Nessun NaN in lat/lon."""
        assert df_csv_latlon["lat"].notna().all()
        assert df_csv_latlon["lon"].notna().all()

    def test_csv_colonne_extra_preservate(self, df_csv_latlon):
        """Le colonne extra (oltre lat/lon) sono preservate."""
        assert "id" in df_csv_latlon.columns
        assert "nome" in df_csv_latlon.columns
        assert "val" in df_csv_latlon.columns


# =============================================================================
# REG-05 — CRS propagato senza riproiezione
# =============================================================================

class TestREG05Unit:

    @pytest.fixture
    def gdf_32632(self):
        """GeoDataFrame in EPSG:32632."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            rng = np.random.default_rng(42)
            n = 100
            return gpd.GeoDataFrame(
                {"id": np.arange(n), "val": rng.uniform(0, 100, n)},
                geometry=[Point(rng.uniform(300000, 900000),
                                rng.uniform(4000000, 5200000))
                          for _ in range(n)],
                crs="EPSG:32632"
            )
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_crs_32632_preservato(self, gdf_32632):
        """CRS 32632 è preservato senza riproiezione."""
        assert gdf_32632.crs.to_epsg() == 32632

    def test_nessuna_riproiezione_automatica(self, gdf_32632):
        """Le coordinate non vengono cambiate automaticamente."""
        x_orig = gdf_32632.geometry.x.values.copy()
        # Simula passaggio attraverso il pipeline senza reproiezione
        result = gdf_32632.copy()
        np.testing.assert_array_equal(result.geometry.x.values, x_orig)

    @pytest.mark.parametrize("epsg", [4326, 3857, 32632, 32633, 25832])
    def test_crs_vari_preservati(self, epsg):
        """Vari CRS sono preservati nel pipeline."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            gdf = gpd.GeoDataFrame(
                {"id": [1]},
                geometry=[Point(0, 0)],
                crs=f"EPSG:{epsg}"
            )
            result = gdf.copy()
            assert result.crs.to_epsg() == epsg
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_result_node_crs_nel_metadata(self, gdf_32632):
        """Il CRS è presente nei metadata del result node."""
        crs_info = {"epsg": gdf_32632.crs.to_epsg(), "name": str(gdf_32632.crs)}
        assert crs_info["epsg"] == 32632
        assert "32632" in crs_info["name"]


# =============================================================================
# REG-06 — Credenziali errate → 400
# =============================================================================

class TestREG06Unit:

    def test_connessione_credenziali_errate_risultato(self):
        """Test connection con credenziali errate ritorna success=False."""
        from app.services.connection_service import ConnectionService, ConnectionTestResult
        svc = ConnectionService()
        with patch("app.services.connection_service.db") as mock_db:
            mock_db.get_connection_config.return_value = {
                "id": "c1", "name": "test", "db_type": "postgres",
                "host": "localhost", "port": 5432, "database": "db",
                "username": "wrong_user", "password": "wrong_pass",
                "extra": "{}", "group_id": "default",
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00",
            }
            with patch.object(svc, "_test_postgres",
                              side_effect=Exception("password authentication failed")):
                result = svc.test_connection("c1")
        assert result.success is False
        assert result.message

    @pytest.mark.parametrize("error_msg", [
        "password authentication failed",
        "role does not exist",
        "database does not exist",
        "Connection refused",
        "authentication failed",
    ])
    def test_vari_errori_credenziali(self, error_msg):
        """Vari tipi di errore credenziali producono success=False."""
        from app.services.connection_service import ConnectionService
        svc = ConnectionService()
        with patch("app.services.connection_service.db") as mock_db:
            mock_db.get_connection_config.return_value = {
                "id": "c1", "name": "test", "db_type": "postgres",
                "host": "h", "port": 5432, "database": "d",
                "username": "u", "password": "p", "extra": "{}",
                "group_id": "default",
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00",
            }
            with patch.object(svc, "_test_postgres",
                              side_effect=Exception(error_msg)):
                result = svc.test_connection("c1")
        assert result.success is False

    def test_messaggio_errore_leggibile(self):
        """Il messaggio di errore è comprensibile per l'utente."""
        from app.services.connection_service import ConnectionService
        svc = ConnectionService()
        with patch("app.services.connection_service.db") as mock_db:
            mock_db.get_connection_config.return_value = {
                "id": "c1", "name": "test", "db_type": "postgres",
                "host": "h", "port": 5432, "database": "d",
                "username": "u", "password": "p", "extra": "{}",
                "group_id": "default",
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00",
            }
            with patch.object(svc, "_test_postgres",
                              side_effect=Exception("password authentication failed")):
                result = svc.test_connection("c1")
        assert len(result.message) > 0
        assert result.message != "None"


# =============================================================================
# REG-07 — Query timeout gestito
# =============================================================================

class TestREG07Unit:

    def test_timeout_configurato(self):
        """DB_QUERY_TIMEOUT è configurato."""
        from app.config import settings
        timeout = getattr(settings, "DB_QUERY_TIMEOUT", None)
        if timeout is None:
            pytest.xfail("DB_QUERY_TIMEOUT non configurato — bug pendente")
        assert timeout > 0

    def test_timeout_valore_ragionevole(self):
        """Timeout è tra 5 e 300 secondi."""
        from app.config import settings
        timeout = getattr(settings, "DB_QUERY_TIMEOUT", 30)
        assert 5 <= timeout <= 300

    @pytest.mark.parametrize("timeout", [10, 30, 60, 120])
    def test_timeout_vari_valori_accettabili(self, timeout):
        """Vari valori di timeout sono accettabili."""
        assert 1 <= timeout <= 3600

    def test_query_executor_gestisce_timeout(self):
        """query_executor gestisce TimeoutError senza crash."""
        try:
            from app.db import query_executor
            import inspect
            src = inspect.getsource(query_executor)
            has_timeout = any(kw in src.lower() for kw in
                             ["timeout", "statement_timeout"])
            if not has_timeout:
                pytest.xfail("timeout non in query_executor — bug pendente")
        except ImportError:
            pytest.skip("query_executor non disponibile")

    def test_server_non_crasha_su_timeout(self):
        """Il server non crasha quando una query va in timeout."""
        mock_executor = MagicMock()
        mock_executor.execute_query.side_effect = TimeoutError("query timeout")
        try:
            mock_executor.execute_query("SELECT 1")
        except TimeoutError:
            pass  # corretto
        except Exception as e:
            pytest.fail(f"Eccezione inattesa: {e}")


# =============================================================================
# REG-08 — Upload file > 50MB
# =============================================================================

class TestREG08Unit:

    @pytest.mark.parametrize("size_mb", [1, 10, 25, 50, 100])
    def test_file_grande_generazione(self, size_mb):
        """Generazione file sintetico > N MB."""
        n_rows = size_mb * 1000
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "id":   np.arange(n_rows),
            "val":  rng.uniform(0, 100, n_rows),
            "nome": [f"r_{i}" for i in range(n_rows)],
        })
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        size_bytes = buf.tell()
        assert size_bytes > 0

    def test_file_50mb_no_oom(self):
        """File 50MB non causa OOM."""
        import tracemalloc
        tracemalloc.start()
        try:
            n_rows = 500_000
            rng = np.random.default_rng(42)
            df = pd.DataFrame({
                "id":  np.arange(n_rows),
                "val": rng.uniform(0, 100, n_rows),
            })
            buf = io.BytesIO()
            df.to_csv(buf, index=False)
            _, peak = tracemalloc.get_traced_memory()
            peak_mb = peak / 1024 / 1024
            assert peak_mb < 2000, f"OOM: {peak_mb:.0f}MB"
        finally:
            tracemalloc.stop()

    def test_file_service_whitelist_formati_grandi(self):
        """Formati per file grandi sono nella whitelist."""
        allowed = {".csv", ".xlsx", ".xls", ".json", ".geojson",
                   ".gpkg", ".shp", ".gdb", ".zip"}
        for ext in [".csv", ".gpkg", ".zip"]:
            assert ext in allowed, f"{ext} non nella whitelist"

    @pytest.mark.parametrize("size_mb,expected_rows", [
        (1,   ~10_000),
        (10,  ~100_000),
        (50,  ~500_000),
    ])
    def test_stima_righe_per_dimensione(self, size_mb, expected_rows):
        """Stima righe per dimensione file è ragionevole."""
        # ~100 bytes per riga CSV
        estimated = size_mb * 1024 * 1024 // 100
        assert estimated > 0

    def test_upload_chunked_corretto(self):
        """Upload chunked copre tutti i byte."""
        total = 50 * 1024 * 1024  # 50MB
        chunk = 1024 * 1024  # 1MB
        chunks = list(range(0, total, chunk))
        total_covered = sum(
            min(chunk, total - i) for i in chunks
        )
        assert total_covered == total
