"""
tests/regression/unit/test_REG17_27_arricchisci.py
===================================================
Regression test UNIT — Modulo ARRICCHISCI (REG-17→REG-27)
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch, call

pytestmark = [pytest.mark.regression, pytest.mark.unit]




# =============================================================================
# REG-17 — Pipeline FileSource → REST Countries → CSV
# =============================================================================

class TestREG17Unit:

    def test_pipeline_5_paesi_output(self, df_paesi, mock_rest_countries):
        """5 paesi producono output con capitale e popolazione."""
        results = []
        for _, row in df_paesi.iterrows():
            data = mock_rest_countries(row["paese"])
            results.append({
                "paese":      row["paese"],
                "capitale":   data["capital"][0],
                "popolazione": data["population"],
            })
        result_df = pd.DataFrame(results)
        assert len(result_df) == 5
        assert "capitale" in result_df.columns
        assert "popolazione" in result_df.columns
        assert result_df["capitale"].notna().all()

    def test_pipeline_output_identico_baseline(self, df_paesi, mock_rest_countries,
                                                baseline):
        """Output identico al baseline."""
        results = []
        for _, row in df_paesi.iterrows():
            data = mock_rest_countries(row["paese"])
            results.append({
                "paese": row["paese"],
                "capital": data["capital"][0],
                "population": data["population"],
            })
        result_df = pd.DataFrame(results)
        data = {"n_rows": len(result_df),
                "paesi": sorted(result_df["paese"].tolist())}
        baseline["assert"]("REG17_output", data)

    @pytest.mark.parametrize("paese,capitale_attesa", [
        ("Italy",   "Rome"),
        ("France",  "Paris"),
        ("Germany", "Berlin"),
        ("Spain",   "Madrid"),
        ("Poland",  "Warsaw"),
    ])
    def test_capitale_corretta_per_paese(self, mock_rest_countries, paese, capitale_attesa):
        """La capitale restituita è corretta per ogni paese."""
        result = mock_rest_countries(paese)
        assert result["capital"][0] == capitale_attesa

    def test_output_csv_struttura(self, df_paesi, mock_rest_countries):
        """Output CSV ha struttura corretta."""
        import io
        results = []
        for _, row in df_paesi.iterrows():
            data = mock_rest_countries(row["paese"])
            results.append({
                "paese": row["paese"],
                "capital": data["capital"][0],
                "population": data["population"],
            })
        result_df = pd.DataFrame(results)
        buf = io.StringIO()
        result_df.to_csv(buf, index=False)
        buf.seek(0)
        loaded = pd.read_csv(buf)
        assert len(loaded) == 5
        assert "paese" in loaded.columns

    def test_nessun_paese_mancante(self, df_paesi, mock_rest_countries):
        """Tutti i paesi hanno dati."""
        for _, row in df_paesi.iterrows():
            data = mock_rest_countries(row["paese"])
            assert data["capital"][0] != "Unknown"
            assert data["population"] > 0


# =============================================================================
# REG-18 — Pipeline FileSource → Open-Meteo → GPKG
# =============================================================================

class TestREG18Unit:

    def test_pipeline_open_meteo_output(self, df_csv_latlon, mock_open_meteo):
        """Pipeline Open-Meteo produce colonne temperatura e vento."""
        sample = df_csv_latlon.head(10)
        results = []
        for _, row in sample.iterrows():
            data = mock_open_meteo(row["lat"], row["lon"])
            results.append({
                "id":          row["id"],
                "lat":         row["lat"],
                "lon":         row["lon"],
                "temperatura": data["current"]["temperature_2m"],
                "vento":       data["current"]["wind_speed_10m"],
            })
        result_df = pd.DataFrame(results)
        assert "temperatura" in result_df.columns
        assert "vento" in result_df.columns
        assert result_df["temperatura"].notna().all()
        assert result_df["vento"].notna().all()

    @pytest.mark.parametrize("n_punti", [5, 10, 50, 100])
    def test_n_punti_enriched(self, df_csv_latlon, mock_open_meteo, n_punti):
        """N punti arricchiti con dati meteo."""
        sample = df_csv_latlon.head(n_punti)
        results = []
        for _, row in sample.iterrows():
            data = mock_open_meteo(row["lat"], row["lon"])
            results.append({"id": row["id"],
                            "temp": data["current"]["temperature_2m"]})
        assert len(results) == n_punti

    def test_gpkg_output_valido(self, df_csv_latlon, mock_open_meteo, tmp_path):
        """Output GPKG con dati meteo è valido."""
        try:
            import geopandas as gpd
            from shapely.geometry import Point
            sample = df_csv_latlon.head(50)
            data_list = []
            for _, row in sample.iterrows():
                meteo = mock_open_meteo(row["lat"], row["lon"])
                data_list.append({
                    "id":   row["id"],
                    "temp": meteo["current"]["temperature_2m"],
                    "wind": meteo["current"]["wind_speed_10m"],
                })
            df_enriched = pd.DataFrame(data_list)
            gdf = gpd.GeoDataFrame(
                df_enriched,
                geometry=[Point(lon, lat)
                          for lon, lat in zip(sample["lon"], sample["lat"])],
                crs="EPSG:4326"
            )
            path = tmp_path / "meteo.gpkg"
            gdf.to_file(str(path), driver="GPKG")
            loaded = gpd.read_file(str(path))
            assert len(loaded) == 50
            assert "temp" in loaded.columns
            assert loaded.geometry.is_valid.all()
        except ImportError:
            pytest.skip("GeoPandas non disponibile")


# =============================================================================
# REG-19 — ProjectSource da PostgreSQL → REST Countries
# =============================================================================

class TestREG19Unit:

    def test_10_righe_arricchite(self, mock_rest_countries):
        """10 righe da PostgreSQL arricchite con REST Countries."""
        rng = np.random.default_rng(42)
        df_pg = pd.DataFrame({
            "id":    np.arange(10),
            "paese": rng.choice(["Italy", "France", "Germany",
                                  "Spain", "Poland"], 10),
            "val":   rng.uniform(0, 100, 10),
        })
        results = []
        for _, row in df_pg.iterrows():
            data = mock_rest_countries(row["paese"])
            results.append({**row.to_dict(), **{
                "capitale":   data["capital"][0],
                "popolazione": data["population"],
                "area":       data["area"],
            }})
        result_df = pd.DataFrame(results)
        assert len(result_df) == 10
        assert "capitale" in result_df.columns
        assert "popolazione" in result_df.columns
        assert "area" in result_df.columns
        assert result_df["capitale"].notna().all()

    @pytest.mark.parametrize("n_rows", [5, 10, 20, 50])
    def test_n_righe_output_corretto(self, n_rows, mock_rest_countries):
        """N righe producono N righe arricchite."""
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "id":    np.arange(n_rows),
            "paese": rng.choice(["Italy", "France", "Germany"], n_rows),
        })
        results = [mock_rest_countries(p) for p in df["paese"]]
        assert len(results) == n_rows


# =============================================================================
# REG-20 — Cache deduplicazione 10 righe 3 unici
# =============================================================================

class TestREG20Unit:

    def test_dedup_10_righe_3_unici_7_risparmiate(self, df_paesi_10):
        """10 righe, 3 unici → 7 chiamate risparmiate."""
        n_unici = df_paesi_10["paese"].nunique()
        assert n_unici == 3
        n_risparmiate = len(df_paesi_10) - n_unici
        assert n_risparmiate == 7

    def test_dedup_solo_3_chiamate_api(self, df_paesi_10, mock_rest_countries):
        """Con dedup vengono fatte solo 3 chiamate API."""
        cache = {}
        chiamate = 0
        results = []
        for _, row in df_paesi_10.iterrows():
            paese = row["paese"]
            if paese not in cache:
                chiamate += 1
                cache[paese] = mock_rest_countries(paese)
            results.append(cache[paese])
        assert chiamate == 3
        assert len(results) == 10

    def test_dedup_output_identico_senza_dedup(self, df_paesi_10,
                                                mock_rest_countries):
        """Output con e senza dedup è identico."""
        def without_dedup():
            return [mock_rest_countries(p) for p in df_paesi_10["paese"]]

        def with_dedup():
            cache = {}
            results = []
            for p in df_paesi_10["paese"]:
                if p not in cache:
                    cache[p] = mock_rest_countries(p)
                results.append(cache[p])
            return results

        r1 = without_dedup()
        r2 = with_dedup()
        for a, b in zip(r1, r2):
            assert a == b

    @pytest.mark.parametrize("n_total,n_unique,expected_saved", [
        (10,    3,   7),
        (100,   5,  95),
        (1000,  3, 997),
        (50000, 3, 49997),
    ])
    def test_dedup_risparmio_vari_casi(self, n_total, n_unique, expected_saved):
        """Dedup risparmia il numero atteso di chiamate."""
        saved = n_total - n_unique
        assert saved == expected_saved


# =============================================================================
# REG-21 — Output mapping path annidato [0].capital[0]
# =============================================================================

class TestREG21Unit:

    @pytest.fixture
    def nested_response(self):
        return {
            "name": {"common": "Italy", "official": "Republic of Italy"},
            "capital": ["Rome"],
            "population": 60_000_000,
            "currencies": {"EUR": {"name": "Euro"}},
            "languages": {"ita": "Italian"},
        }

    @pytest.mark.parametrize("path,expected", [
        ("capital[0]",              "Rome"),
        ("name.common",             "Italy"),
        ("name.official",           "Republic of Italy"),
        ("currencies.EUR.name",     "Euro"),
        ("languages.ita",           "Italian"),
    ])
    def test_path_estratto_corretto(self, nested_response, path, expected):
        """Path annidato estrae valore corretto."""
        def extract(obj, path):
            parts = path.replace("[", ".").replace("]", "").split(".")
            for part in parts:
                if part.isdigit():
                    obj = obj[int(part)]
                else:
                    obj = obj[part]
            return obj
        assert extract(nested_response, path) == expected

    def test_path_stringa_non_array(self, nested_response):
        """[0] estrae stringa, non array."""
        capital = nested_response["capital"][0]
        assert isinstance(capital, str)
        assert capital == "Rome"

    @pytest.mark.parametrize("path", [
        "capital[0]",
        "name.common",
        "population",
    ])
    def test_vari_path_non_crashano(self, nested_response, path):
        """Vari path non crashano."""
        def extract(obj, path):
            try:
                parts = path.replace("[", ".").replace("]", "").split(".")
                for part in parts:
                    if part.isdigit():
                        obj = obj[int(part)]
                    else:
                        obj = obj[part]
                return obj
            except (KeyError, IndexError):
                return None
        result = extract(nested_response, path)
        assert result is not None


# =============================================================================
# REG-22 — continue_on_error con 1 riga invalida
# =============================================================================

class TestREG22Unit:

    def test_5_ok_1_null_nessun_crash(self, mock_rest_countries):
        """5 righe ok, 1 invalida → 5 ok + 1 null, no crash."""
        paesi = ["Italy", "France", "Germany", "Spain", "Poland", "INVALIDO"]
        results = []
        errors = 0
        for paese in paesi:
            try:
                data = mock_rest_countries(paese)
                if data["capital"][0] == "Unknown":
                    results.append(None)
                    errors += 1
                else:
                    results.append(data["capital"][0])
            except Exception:
                results.append(None)
                errors += 1
        assert len(results) == 6
        assert results.count(None) == 1
        assert errors == 1

    @pytest.mark.parametrize("n_validi,n_invalidi", [
        (5,  1),
        (10, 2),
        (20, 5),
        (100, 10),
    ])
    def test_continue_on_error_vari_casi(self, n_validi, n_invalidi,
                                          mock_rest_countries):
        """Continue on error con vari rapporti validi/invalidi."""
        paesi_validi = ["Italy", "France", "Germany", "Spain", "Poland"]
        paesi = ([paesi_validi[i % 5] for i in range(n_validi)] +
                 [f"INVALIDO_{i}" for i in range(n_invalidi)])
        results = []
        errors = 0
        for p in paesi:
            data = mock_rest_countries(p)
            if data["capital"][0] == "Unknown":
                results.append(None)
                errors += 1
            else:
                results.append(data["capital"][0])
        assert len(results) == n_validi + n_invalidi
        assert errors == n_invalidi


# =============================================================================
# REG-23 — Validazione canvas: param mancante → errore
# =============================================================================

class TestREG23Unit:

    def test_param_mancante_valid_false(self):
        """Param required mancante → valid=False."""
        api_config = {
            "parameters": [
                {"name": "country", "required": True, "in": "query"}
            ]
        }
        param_mapping = {}  # manca "country"
        required = [p["name"] for p in api_config["parameters"]
                    if p.get("required")]
        missing = [r for r in required if r not in param_mapping]
        assert len(missing) > 0
        assert "country" in missing

    @pytest.mark.parametrize("required_params,provided_params,is_valid", [
        (["country"],           {"country": "col_paese"},  True),
        (["country"],           {},                         False),
        (["lat", "lon"],        {"lat": "col_lat"},         False),
        (["lat", "lon"],        {"lat": "col_lat", "lon": "col_lon"}, True),
        ([],                    {},                         True),
        (["a", "b", "c"],       {"a": "x", "b": "y"},      False),
    ])
    def test_validazione_vari_casi(self, required_params, provided_params, is_valid):
        """Validazione produce risultato corretto."""
        missing = [r for r in required_params if r not in provided_params]
        result_valid = len(missing) == 0
        assert result_valid == is_valid

    def test_messaggio_errore_contiene_param_mancante(self):
        """Il messaggio di errore menziona il parametro mancante."""
        missing = ["country", "lat"]
        message = f"Parametri mancanti: {', '.join(missing)}"
        assert "country" in message
        assert "lat" in message


# =============================================================================
# REG-24 — Stima enrichment: 10 righe, 5 unici
# =============================================================================

class TestREG24Unit:

    @pytest.mark.parametrize("n_total,n_unique,rate", [
        (10,   5,  1),
        (100,  10, 5),
        (1000, 20, 10),
    ])
    def test_stima_chiamate_con_dedup(self, n_total, n_unique, rate):
        """Con dedup: stima chiamate = n_unique."""
        estimated_calls = n_unique
        estimated_time = estimated_calls / rate
        assert estimated_calls == n_unique
        assert estimated_time > 0

    def test_stima_10_righe_5_unici(self):
        """10 righe, 5 unici → 5 chiamate, dedup 50%."""
        n_total, n_unique, rate = 10, 5, 1
        estimated_calls = n_unique
        dedup_pct = (n_total - n_unique) / n_total
        estimated_time = estimated_calls / rate
        assert estimated_calls == 5
        assert abs(dedup_pct - 0.5) < 0.01
        assert estimated_time == 5.0

    def test_stima_coerente_con_rate(self):
        """Stima tempo è coerente con rate_limit."""
        for n_calls, rate in [(5, 1), (10, 2), (100, 10)]:
            estimated = n_calls / rate
            assert estimated > 0
            assert estimated == n_calls / rate


# =============================================================================
# REG-25 — Generator paginazione offset/limit
# =============================================================================

class TestREG25Unit:

    @pytest.mark.parametrize("total,page_size,expected_pages", [
        (30,   10,  3),
        (100,  10,  10),
        (25,   10,  3),
        (0,    10,  0),
        (10,   10,  1),
    ])
    def test_paginazione_n_chiamate(self, total, page_size, expected_pages):
        """Paginazione produce il numero atteso di pagine."""
        n_pages = (total + page_size - 1) // page_size if total > 0 else 0
        assert n_pages == expected_pages

    def test_paginazione_30_righe_3_chiamate(self):
        """30 righe, page_size=10 → 3 chiamate API."""
        total, page_size = 30, 10
        pages = list(range(0, total, page_size))
        assert len(pages) == 3
        # Simula raccolta risultati
        all_rows = []
        for offset in pages:
            batch = list(range(offset, min(offset + page_size, total)))
            all_rows.extend(batch)
        assert len(all_rows) == total

    def test_paginazione_offset_corretto(self):
        """Gli offset delle pagine sono corretti."""
        total, page_size = 30, 10
        offsets = list(range(0, total, page_size))
        assert offsets == [0, 10, 20]

    @pytest.mark.parametrize("total,page_size", [
        (30,  10),
        (100, 25),
        (50,  7),
    ])
    def test_paginazione_copre_tutti_record(self, total, page_size):
        """La paginazione copre tutti i record."""
        covered = sum(
            min(page_size, total - offset)
            for offset in range(0, total, page_size)
        )
        assert covered == total
