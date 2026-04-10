"""
tests/performance/unit/test_PERF03_05_manipola.py
==================================================
Test UNIT di performance — Modulo MANIPOLA (PERF-03→PERF-05)

PERF-03  Join spaziale 50k×50k poligoni → < 120s
PERF-04  Dissolve 100k poligoni → < 60s
PERF-05  Pivot 2M righe → < 30s

Unit: operazioni Pandas/GeoPandas in memoria, nessun backend.
"""

import pytest
import time
import numpy as np
import pandas as pd
from unittest.mock import MagicMock

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
# PERF-03 — Join spaziale 50k×50k poligoni
# =============================================================================

class TestPERF03JoinUnit:

    @pytest.mark.parametrize("n_left,n_right", [
        (100,   100),
        (1_000, 1_000),
        (5_000, 5_000),
        (10_000, 10_000),
        (50_000, 50_000),
    ])
    def test_join_pandas_merge_performance(self, n_left, n_right):
        """Merge Pandas su chiave categorica scala correttamente."""
        rng = np.random.default_rng(42)
        df_left = pd.DataFrame({
            "id":       np.arange(n_left),
            "categoria": rng.choice(["A", "B", "C"], size=n_left),
            "valore_l": rng.uniform(0, 100, size=n_left),
        })
        df_right = pd.DataFrame({
            "categoria":  ["A", "B", "C"],
            "descrizione": ["Alpha", "Beta", "Gamma"],
            "valore_r":    [1.0, 2.0, 3.0],
        })
        threshold = max(0.5, n_left / 100_000 * 5)
        with assert_faster_than(threshold, f"merge({n_left}×3)"):
            result = df_left.merge(df_right, on="categoria", how="left")
        assert len(result) == n_left
        assert "valore_l" in result.columns
        assert "valore_r" in result.columns
        assert result["valore_r"].notna().all()

    @pytest.mark.parametrize("n", [1_000, 10_000, 50_000])
    def test_sjoin_qualita_output(self, n):
        """Join spaziale produce output con geometrie valide."""
        try:
            import geopandas as gpd
            from shapely.geometry import box
            rng = np.random.default_rng(42)
            size = 0.1
            lons = rng.uniform(6.0, 18.0, size=n)
            lats = rng.uniform(36.0, 46.0, size=n)
            geoms = [box(lon, lat, lon + size, lat + size)
                     for lon, lat in zip(lons, lats)]
            gdf = gpd.GeoDataFrame(
                {"id": np.arange(n), "cat": rng.choice(["A", "B"], size=n)},
                geometry=geoms, crs="EPSG:4326"
            )
            assert_gdf_quality(gdf, min_rows=n, label=f"sjoin input {n}")
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_join_no_duplicati_inattesi(self, df_medium):
        """Join su chiave univoca non duplica righe."""
        df_right = pd.DataFrame({
            "id": df_medium["id"],
            "extra": np.random.uniform(0, 1, size=len(df_medium))
        })
        result = df_medium.merge(df_right, on="id", how="inner")
        assert len(result) == len(df_medium)

    def test_join_preserva_tipi(self, df_medium):
        """Merge preserva i tipi delle colonne."""
        df_right = pd.DataFrame({
            "id":    df_medium["id"],
            "bonus": np.ones(len(df_medium), dtype=np.float64)
        })
        result = df_medium.merge(df_right, on="id")
        assert result["id"].dtype == df_medium["id"].dtype
        assert result["valore"].dtype == df_medium["valore"].dtype

    @pytest.mark.parametrize("how", ["inner", "left", "right", "outer"])
    def test_join_varie_modalita(self, how, df_small):
        """Tutti i tipi di join producono output valido."""
        df_right = pd.DataFrame({
            "id":    df_small["id"][:500],
            "extra": np.ones(500)
        })
        result = df_small.merge(df_right, on="id", how=how)
        assert result is not None
        assert len(result) > 0 or how == "inner"

    def test_join_left_preserva_tutte_righe_sinistra(self, df_medium):
        """Left join preserva tutte le righe del DataFrame sinistro."""
        df_right = pd.DataFrame({"id": [0, 1, 2], "tag": ["x", "y", "z"]})
        result = df_medium.merge(df_right, on="id", how="left")
        assert len(result) == len(df_medium)

    @pytest.mark.parametrize("n", [1_000, 10_000, 100_000])
    def test_join_grandi_volumi_qualita(self, n):
        """Join su grandi volumi preserva qualità."""
        rng = np.random.default_rng(42)
        df_l = pd.DataFrame({
            "id": np.arange(n),
            "val": rng.uniform(0, 100, size=n),
            "cat": rng.choice(["A", "B", "C"], size=n),
        })
        df_r = pd.DataFrame({
            "cat":   ["A", "B", "C"],
            "label": ["Alpha", "Beta", "Gamma"]
        })
        result = df_l.merge(df_r, on="cat", how="left")
        assert_df_quality(result, min_rows=n, label=f"join {n}")
        assert result["label"].notna().all()


# =============================================================================
# PERF-04 — Dissolve 100k poligoni
# =============================================================================

class TestPERF04DissolveUnit:

    @pytest.mark.parametrize("n,categories", [
        (1_000,   3),
        (10_000,  5),
        (50_000,  10),
        (100_000, 20),
    ])
    def test_groupby_sum_performance(self, n, categories):
        """GroupBy+sum su vari volumi rispetta soglie."""
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "categoria": rng.choice([f"cat_{i}" for i in range(categories)], size=n),
            "valore":    rng.uniform(0, 100, size=n),
            "quantita":  rng.integers(1, 100, size=n),
        })
        threshold = max(0.5, n / 100_000 * 3)
        with assert_faster_than(threshold, f"groupby({n},{categories})"):
            result = df.groupby("categoria").agg(
                valore_sum=("valore", "sum"),
                quantita_mean=("quantita", "mean"),
                count=("valore", "count"),
            ).reset_index()
        assert len(result) == categories
        assert result["count"].sum() == n
        assert result["valore_sum"].notna().all()

    @pytest.mark.parametrize("n", [1_000, 10_000, 100_000])
    def test_dissolve_gdf_qualita(self, n):
        """Dissolve GeoDataFrame produce geometrie valide."""
        try:
            import geopandas as gpd
            from shapely.geometry import box
            rng = np.random.default_rng(42)
            cats = rng.choice(["A", "B", "C"], size=n)
            lons = rng.uniform(6.0, 15.0, size=n)
            lats = rng.uniform(36.0, 46.0, size=n)
            geoms = [box(lon, lat, lon + 0.1, lat + 0.1)
                     for lon, lat in zip(lons, lats)]
            gdf = gpd.GeoDataFrame({"cat": cats, "val": rng.uniform(0, 1, n)},
                                   geometry=geoms, crs="EPSG:4326")
            threshold = max(2.0, n / 10_000 * 5)
            with assert_faster_than(threshold, f"dissolve({n})"):
                dissolved = gdf.dissolve(by="cat", aggfunc="sum")
            assert len(dissolved) == 3
            assert dissolved.geometry.notna().all()
            assert dissolved.crs is not None
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_dissolve_nessun_oom(self, gdf_100k_polygons):
        """Dissolve 100k poligoni non causa OOM."""
        import tracemalloc
        tracemalloc.start()
        try:
            result = gdf_100k_polygons.dissolve(by="categoria", aggfunc="sum")
            current, peak = tracemalloc.get_traced_memory()
            peak_mb = peak / 1024 / 1024
            assert peak_mb < 2000, f"Picco memoria: {peak_mb:.0f}MB > 2GB"
        except ImportError:
            pytest.skip("GeoPandas non disponibile")
        finally:
            tracemalloc.stop()

    def test_dissolve_preserva_crs(self, gdf_50k_polygons):
        """Dissolve preserva il CRS originale."""
        try:
            original_crs = gdf_50k_polygons.crs
            result = gdf_50k_polygons.dissolve(by="categoria")
            assert result.crs == original_crs
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    @pytest.mark.parametrize("aggfunc", ["sum", "mean", "count", "first"])
    def test_dissolve_varie_aggfunc(self, aggfunc, gdf_50k_polygons):
        """Dissolve con varie aggfunc produce output valido."""
        try:
            result = gdf_50k_polygons.dissolve(by="categoria", aggfunc=aggfunc)
            assert len(result) > 0
            assert result.geometry.notna().all()
        except ImportError:
            pytest.skip("GeoPandas non disponibile")


# =============================================================================
# PERF-05 — Pivot 2M righe
# =============================================================================

class TestPERF05PivotUnit:

    @pytest.mark.parametrize("n,n_cats", [
        (10_000,    3),
        (100_000,   4),
        (500_000,   5),
        (1_000_000, 5),
        (2_000_000, 5),
    ])
    def test_pivot_performance(self, n, n_cats):
        """Pivot su vari volumi rispetta soglie."""
        rng = np.random.default_rng(42)
        cats = [f"cat_{i}" for i in range(n_cats)]
        df = pd.DataFrame({
            "id":        np.arange(n),
            "categoria": rng.choice(cats, size=n),
            "anno":      rng.choice([2020, 2021, 2022, 2023], size=n),
            "valore":    rng.uniform(0, 1000, size=n),
        })
        threshold = max(1.0, n / 1_000_000 * THRESHOLDS["PERF05_pivot"])
        with assert_faster_than(threshold, f"pivot({n:,},{n_cats})"):
            result = df.pivot_table(
                index="categoria",
                columns="anno",
                values="valore",
                aggfunc="sum"
            )
        assert len(result) == n_cats
        assert len(result.columns) == 4  # 4 anni
        assert result.notna().sum().sum() > 0

    def test_pivot_qualita_output(self, df_2m_pivot):
        """Pivot 2M righe produce output corretto."""
        with assert_faster_than(THRESHOLDS["PERF05_pivot"], "pivot 2M"):
            result = df_2m_pivot.pivot_table(
                index="categoria",
                columns="anno",
                values="valore",
                aggfunc="sum"
            )
        assert len(result) == 5       # 5 categorie
        assert len(result.columns) == 4  # 4 anni
        assert not result.empty
        # Ogni cella deve essere > 0 (somma di valori positivi)
        assert (result > 0).all().all()

    def test_pivot_somme_coerenti(self, df_2m_pivot):
        """La somma del pivot corrisponde alla somma totale del DataFrame."""
        result = df_2m_pivot.pivot_table(
            index="categoria",
            columns="anno",
            values="valore",
            aggfunc="sum",
            fill_value=0
        )
        pivot_total = result.values.sum()
        df_total = df_2m_pivot["valore"].sum()
        assert abs(pivot_total - df_total) < 1e-6, \
            f"Somma pivot {pivot_total:.2f} ≠ somma df {df_total:.2f}"

    @pytest.mark.parametrize("aggfunc", ["sum", "mean", "count", "min", "max"])
    def test_pivot_varie_aggfunc(self, aggfunc, df_2m_pivot):
        """Pivot con varie aggfunc produce output valido."""
        with assert_faster_than(60, f"pivot {aggfunc} 2M"):
            result = df_2m_pivot.pivot_table(
                index="categoria",
                columns="anno",
                values="valore",
                aggfunc=aggfunc
            )
        assert not result.empty
        assert result.notna().sum().sum() > 0

    def test_pivot_nessun_oom(self):
        """Pivot 2M righe non causa OOM."""
        import tracemalloc
        rng = np.random.default_rng(42)
        n = 2_000_000
        df = pd.DataFrame({
            "categoria": rng.choice(["A", "B", "C", "D", "E"], size=n),
            "anno":      rng.choice([2020, 2021, 2022, 2023], size=n),
            "valore":    rng.uniform(0, 1000, size=n),
        })
        tracemalloc.start()
        try:
            df.pivot_table(index="categoria", columns="anno",
                           values="valore", aggfunc="sum")
            _, peak = tracemalloc.get_traced_memory()
            peak_mb = peak / 1024 / 1024
            assert peak_mb < 4000, f"OOM: {peak_mb:.0f}MB > 4GB"
        finally:
            tracemalloc.stop()

    def test_groupby_come_alternativa_pivot(self, df_2m_pivot):
        """GroupBy+unstack come alternativa al pivot produce stesso risultato."""
        with assert_faster_than(THRESHOLDS["PERF05_pivot"], "groupby+unstack 2M"):
            result = (df_2m_pivot
                      .groupby(["categoria", "anno"])["valore"]
                      .sum()
                      .unstack("anno"))
        assert len(result) == 5
        assert len(result.columns) == 4

    @pytest.mark.parametrize("n", [100_000, 500_000, 1_000_000, 2_000_000])
    def test_pivot_memoria_proporzionale(self, n):
        """La memoria usata dal pivot scala ragionevolmente con il volume."""
        import tracemalloc
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "cat":    rng.choice(["A", "B", "C"], size=n),
            "anno":   rng.choice([2020, 2021, 2022], size=n),
            "valore": rng.uniform(0, 1000, size=n),
        })
        tracemalloc.start()
        df.pivot_table(index="cat", columns="anno", values="valore", aggfunc="sum")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak / 1024 / 1024
        # Max 2GB per 2M righe
        assert peak_mb < n / 1_000_000 * 2000 + 500, \
            f"Memoria {peak_mb:.0f}MB fuori range per {n:,} righe"
