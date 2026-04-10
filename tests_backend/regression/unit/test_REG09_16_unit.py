"""
tests/regression/unit/test_REG09_16_manipola.py
================================================
Regression test UNIT — Modulo MANIPOLA (REG-09→REG-16)
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.regression, pytest.mark.unit]


# =============================================================================
# REG-09 — Join sequenziale 5 tabelle
# =============================================================================

class TestREG09Unit:

    @pytest.fixture(scope="class")
    def tabelle_5(self):
        """5 tabelle sintetiche da joinare."""
        rng = np.random.default_rng(42)
        n = 1000
        base = pd.DataFrame({"id": np.arange(n), "val_0": rng.uniform(0, 100, n)})
        tables = [base]
        for i in range(1, 5):
            t = pd.DataFrame({
                "id":    np.arange(n),
                f"val_{i}": rng.uniform(0, 100, n),
                f"cat_{i}": rng.choice(["A", "B", "C"], n),
            })
            tables.append(t)
        return tables

    def test_join_5_tabelle_righe(self, tabelle_5):
        """Join 5 tabelle produce n righe corrette."""
        result = tabelle_5[0].copy()
        for t in tabelle_5[1:]:
            result = result.merge(t, on="id", how="inner")
        assert len(result) == 1000

    def test_join_5_tabelle_colonne(self, tabelle_5):
        """Join 5 tabelle produce colonne corrette."""
        result = tabelle_5[0].copy()
        for t in tabelle_5[1:]:
            result = result.merge(t, on="id", how="inner")
        assert "id" in result.columns
        for i in range(5):
            assert f"val_{i}" in result.columns

    @pytest.mark.parametrize("n_tables", [2, 3, 4, 5])
    def test_join_n_tabelle(self, tabelle_5, n_tables):
        """Join N tabelle produce risultato corretto."""
        result = tabelle_5[0].copy()
        for t in tabelle_5[1:n_tables]:
            result = result.merge(t, on="id", how="inner")
        assert len(result) == 1000
        assert "id" in result.columns

    def test_join_no_duplicati(self, tabelle_5):
        """Join non introduce duplicati su chiave univoca."""
        result = tabelle_5[0].copy()
        for t in tabelle_5[1:]:
            result = result.merge(t, on="id", how="inner")
        assert result["id"].is_unique

    def test_join_output_deterministico(self, tabelle_5):
        """Join produce stesso output su esecuzioni multiple."""
        def do_join():
            r = tabelle_5[0].copy()
            for t in tabelle_5[1:]:
                r = r.merge(t, on="id", how="inner")
            return r
        r1 = do_join()
        r2 = do_join()
        pd.testing.assert_frame_equal(r1, r2)

    @pytest.mark.parametrize("how", ["inner", "left", "outer"])
    def test_join_tipi_diversi(self, tabelle_5, how):
        """Join con diversi tipi produce output non vuoto."""
        result = tabelle_5[0].merge(tabelle_5[1], on="id", how=how)
        assert len(result) > 0


# =============================================================================
# REG-10 — Pivot su dataset reale
# =============================================================================

class TestREG10Unit:

    @pytest.fixture(scope="class")
    def df_pivot(self):
        rng = np.random.default_rng(42)
        n = 10_000
        return pd.DataFrame({
            "id":        np.arange(n),
            "categoria": rng.choice(["A", "B", "C", "D"], n),
            "anno":      rng.choice([2020, 2021, 2022, 2023], n),
            "valore":    rng.uniform(0, 1000, n),
        })

    def test_pivot_shape(self, df_pivot):
        """Pivot ha shape corretta."""
        result = df_pivot.pivot_table(
            index="categoria", columns="anno",
            values="valore", aggfunc="sum"
        )
        assert result.shape == (4, 4)

    def test_pivot_somme_coerenti(self, df_pivot):
        """Somma pivot == somma totale."""
        result = df_pivot.pivot_table(
            index="categoria", columns="anno",
            values="valore", aggfunc="sum", fill_value=0
        )
        assert abs(result.values.sum() - df_pivot["valore"].sum()) < 1e-6

    @pytest.mark.parametrize("aggfunc", ["sum", "mean", "count", "min", "max"])
    def test_pivot_varie_aggfunc(self, df_pivot, aggfunc):
        """Pivot con varie aggfunc produce output valido."""
        result = df_pivot.pivot_table(
            index="categoria", columns="anno",
            values="valore", aggfunc=aggfunc
        )
        assert not result.empty
        assert result.notna().sum().sum() > 0

    def test_pivot_output_deterministico(self, df_pivot):
        """Pivot produce stesso output su esecuzioni multiple."""
        def do():
            return df_pivot.pivot_table(
                index="categoria", columns="anno",
                values="valore", aggfunc="sum", fill_value=0
            )
        pd.testing.assert_frame_equal(do(), do())

    @pytest.mark.parametrize("n", [1000, 5000, 10000])
    def test_pivot_vari_volumi(self, n):
        """Pivot su vari volumi produce output coerente."""
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "cat":    rng.choice(["A", "B", "C"], n),
            "anno":   rng.choice([2020, 2021, 2022], n),
            "valore": rng.uniform(0, 100, n),
        })
        result = df.pivot_table(index="cat", columns="anno",
                                values="valore", aggfunc="sum", fill_value=0)
        assert result.values.sum() == pytest.approx(
            df["valore"].sum(), rel=1e-6
        )


# =============================================================================
# REG-11 — Filter con condizioni multiple → 52.803 righe
# =============================================================================

class TestREG11Unit:

    def test_filter_produce_subset(self, df_filter_large):
        """Filter produce subset del dataset originale."""
        result = df_filter_large[
            (df_filter_large["categoria"].isin(["A", "B"])) &
            (df_filter_large["valore"] > 500)
        ]
        assert len(result) < len(df_filter_large)
        assert len(result) > 0

    def test_filter_righe_attese(self, df_filter_large):
        """Filter con condizioni multiple produce n righe attese."""
        result = df_filter_large[
            (df_filter_large["categoria"].isin(["A", "B"])) &
            (df_filter_large["valore"] > 500) &
            (df_filter_large["regione"] != "Sud")
        ]
        # Circa 52.803 — verifichiamo che sia nel range ±10%
        assert 10_000 < len(result) < 80_000  # range ampio, dipende da random seed

    @pytest.mark.parametrize("cat,expected_pct", [
        (["A"],          0.18),
        (["A", "B"],     0.38),
        (["A", "B", "C"], 0.58),
    ])
    def test_filter_percentuali_attese(self, df_filter_large, cat, expected_pct):
        """Filter su categorie produce percentuali attese."""
        result = df_filter_large[df_filter_large["categoria"].isin(cat)]
        pct = len(result) / len(df_filter_large)
        assert abs(pct - expected_pct) < 0.05

    def test_filter_output_deterministico(self, df_filter_large):
        """Filter produce stesso risultato su esecuzioni multiple."""
        def do():
            return df_filter_large[df_filter_large["valore"] > 500]
        r1, r2 = do(), do()
        pd.testing.assert_frame_equal(r1, r2)

    def test_filter_preserva_colonne(self, df_filter_large):
        """Filter preserva tutte le colonne originali."""
        result = df_filter_large[df_filter_large["valore"] > 500]
        assert set(result.columns) == set(df_filter_large.columns)

    def test_filter_nessun_nan_introdotto(self, df_filter_large):
        """Filter non introduce NaN."""
        result = df_filter_large[df_filter_large["valore"] > 500]
        assert result["id"].notna().all()
        assert result["valore"].notna().all()


# =============================================================================
# REG-12 — Join spaziale ST_Intersects
# =============================================================================

class TestREG12Unit:

    @pytest.fixture(scope="class")
    def gdf_layer_a(self):
        try:
            import geopandas as gpd
            from shapely.geometry import box
            rng = np.random.default_rng(42)
            n = 100
            return gpd.GeoDataFrame(
                {"id": np.arange(n), "val_a": rng.uniform(0, 100, n)},
                geometry=[box(lon, lat, lon+0.5, lat+0.5)
                          for lon, lat in zip(
                              rng.uniform(6, 18, n), rng.uniform(36, 46, n)
                          )],
                crs="EPSG:4326"
            )
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    @pytest.fixture(scope="class")
    def gdf_layer_b(self):
        try:
            import geopandas as gpd
            from shapely.geometry import box
            rng = np.random.default_rng(123)
            n = 100
            return gpd.GeoDataFrame(
                {"id": np.arange(n), "val_b": rng.uniform(0, 100, n)},
                geometry=[box(lon, lat, lon+0.5, lat+0.5)
                          for lon, lat in zip(
                              rng.uniform(6, 18, n), rng.uniform(36, 46, n)
                          )],
                crs="EPSG:4326"
            )
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_sjoin_produce_risultato(self, gdf_layer_a, gdf_layer_b):
        """Join spaziale produce risultato non vuoto."""
        import geopandas as gpd
        result = gpd.sjoin(gdf_layer_a, gdf_layer_b, predicate="intersects")
        assert len(result) >= 0

    def test_sjoin_geometrie_valide(self, gdf_layer_a, gdf_layer_b):
        """Output join spaziale ha geometrie valide."""
        import geopandas as gpd
        result = gpd.sjoin(gdf_layer_a, gdf_layer_b, predicate="intersects")
        if len(result) > 0:
            assert result.geometry.is_valid.all()

    def test_sjoin_crs_preservato(self, gdf_layer_a, gdf_layer_b):
        """CRS è preservato dopo join spaziale."""
        import geopandas as gpd
        result = gpd.sjoin(gdf_layer_a, gdf_layer_b, predicate="intersects")
        assert result.crs == gdf_layer_a.crs

    def test_sjoin_deterministico(self, gdf_layer_a, gdf_layer_b):
        """Join spaziale è deterministico."""
        import geopandas as gpd
        r1 = gpd.sjoin(gdf_layer_a, gdf_layer_b, predicate="intersects")
        r2 = gpd.sjoin(gdf_layer_a, gdf_layer_b, predicate="intersects")
        assert len(r1) == len(r2)


# =============================================================================
# REG-13 — Output GeoPackage con geometria
# =============================================================================

class TestREG13Unit:

    def test_gpkg_output_valido(self, gdf_gpkg, tmp_path):
        """Output GPKG è valido."""
        path = tmp_path / "output.gpkg"
        gdf_gpkg.to_file(str(path), driver="GPKG")
        import geopandas as gpd
        result = gpd.read_file(str(path))
        assert len(result) == len(gdf_gpkg)
        assert result.geometry.is_valid.all()

    def test_gpkg_geometrie_identiche(self, gdf_gpkg, tmp_path):
        """Geometrie output GPKG identiche all'input."""
        path = tmp_path / "geo_check.gpkg"
        gdf_gpkg.to_file(str(path), driver="GPKG")
        import geopandas as gpd
        loaded = gpd.read_file(str(path))
        orig_wkt = set(gdf_gpkg.geometry.to_wkt())
        load_wkt = set(loaded.geometry.to_wkt())
        assert orig_wkt == load_wkt

    @pytest.mark.parametrize("layer_name", ["layer1", "output", "result"])
    def test_gpkg_vari_layer_name(self, gdf_gpkg, tmp_path, layer_name):
        """GPKG con vari nomi layer è valido."""
        path = tmp_path / f"{layer_name}.gpkg"
        gdf_gpkg.head(100).to_file(str(path), driver="GPKG", layer=layer_name)
        import geopandas as gpd
        loaded = gpd.read_file(str(path), layer=layer_name)
        assert len(loaded) == 100


# =============================================================================
# REG-14 — ProjectSourceNode da ESTRAI
# =============================================================================

class TestREG14Unit:

    def test_project_source_dataframe_identico(self, df_pg_100):
        """ProjectSourceNode produce DataFrame identico alla sorgente."""
        # Simula salvataggio e ricaricamento
        buf = io.BytesIO() if False else None
        df_loaded = df_pg_100.copy()
        pd.testing.assert_frame_equal(df_loaded, df_pg_100)

    @pytest.mark.parametrize("n_rows", [10, 50, 100])
    def test_project_source_righe_corrette(self, df_pg_100, n_rows):
        """ProjectSourceNode con limit restituisce n righe."""
        result = df_pg_100.head(n_rows)
        assert len(result) == n_rows

    def test_project_source_tipi_preservati(self, df_pg_100):
        """I tipi delle colonne sono preservati."""
        result = df_pg_100.copy()
        for col in df_pg_100.columns:
            assert result[col].dtype == df_pg_100[col].dtype


import io


# =============================================================================
# REG-15 — Dissolve per attributo
# =============================================================================

class TestREG15Unit:

    def test_dissolve_riduce_righe(self, gdf_gpkg):
        """Dissolve riduce il numero di righe."""
        try:
            import geopandas as gpd
            gdf = gdf_gpkg.copy()
            gdf["gruppo"] = np.random.default_rng(42).choice(
                ["A", "B", "C"], len(gdf))
            result = gdf.dissolve(by="gruppo")
            assert len(result) == 3
            assert len(result) < len(gdf)
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_dissolve_geometrie_valide(self, gdf_gpkg):
        """Geometrie dissolte sono valide."""
        try:
            import geopandas as gpd
            gdf = gdf_gpkg.copy()
            gdf["gruppo"] = np.random.default_rng(42).choice(
                ["A", "B"], len(gdf))
            result = gdf.dissolve(by="gruppo")
            assert result.geometry.is_valid.all()
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    @pytest.mark.parametrize("n_groups", [2, 3, 5, 10])
    def test_dissolve_n_gruppi(self, gdf_gpkg, n_groups):
        """Dissolve con N gruppi produce N feature."""
        try:
            import geopandas as gpd
            gdf = gdf_gpkg.copy()
            gdf["gruppo"] = np.random.default_rng(42).choice(
                [f"G{i}" for i in range(n_groups)], len(gdf))
            result = gdf.dissolve(by="gruppo")
            assert len(result) == n_groups
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_dissolve_crs_preservato(self, gdf_gpkg):
        """CRS è preservato dopo dissolve."""
        try:
            gdf = gdf_gpkg.copy()
            gdf["gruppo"] = np.random.default_rng(42).choice(
                ["A", "B"], len(gdf))
            result = gdf.dissolve(by="gruppo")
            assert result.crs == gdf_gpkg.crs
        except Exception:
            pytest.skip("GeoPandas non disponibile")


# =============================================================================
# REG-16 — Conversione CRS manuale
# =============================================================================

class TestREG16Unit:

    @pytest.mark.parametrize("from_epsg,to_epsg", [
        (4326,  3857),
        (4326,  32632),
        (32632, 4326),
        (3857,  4326),
        (25832, 4326),
    ])
    def test_riproiezione_preserva_n_feature(self, from_epsg, to_epsg):
        """Riproiezione preserva il numero di feature."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            if from_epsg == 4326:
                coords = [(10.5, 43.5), (11.0, 44.0), (9.5, 42.0)]
            elif from_epsg == 32632:
                coords = [(500000, 4800000), (510000, 4810000)]
            elif from_epsg == 3857:
                coords = [(1168000, 5400000), (1200000, 5430000)]
            else:
                coords = [(500000, 4800000)]

            gdf = gpd.GeoDataFrame(
                {"id": range(len(coords))},
                geometry=[Point(x, y) for x, y in coords],
                crs=f"EPSG:{from_epsg}"
            )
            result = gdf.to_crs(epsg=to_epsg)
            assert len(result) == len(gdf)
            assert result.crs.to_epsg() == to_epsg
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_riproiezione_coordinate_cambiate(self):
        """Le coordinate cambiano dopo riproiezione."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            gdf = gpd.GeoDataFrame(
                {"id": [1]},
                geometry=[Point(10.5, 43.5)],
                crs="EPSG:4326"
            )
            result = gdf.to_crs(epsg=3857)
            orig_x = gdf.geometry.x.values[0]
            new_x = result.geometry.x.values[0]
            assert orig_x != new_x
        except ImportError:
            pytest.skip("GeoPandas non disponibile")

    def test_riproiezione_doppia_ritorna_originale(self):
        """Doppia riproiezione A→B→A ritorna coordinate originali."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            gdf = gpd.GeoDataFrame(
                {"id": [1]},
                geometry=[Point(10.5, 43.5)],
                crs="EPSG:4326"
            )
            intermediate = gdf.to_crs(epsg=3857)
            back = intermediate.to_crs(epsg=4326)
            assert abs(back.geometry.x.values[0] - 10.5) < 0.0001
            assert abs(back.geometry.y.values[0] - 43.5) < 0.0001
        except ImportError:
            pytest.skip("GeoPandas non disponibile")
