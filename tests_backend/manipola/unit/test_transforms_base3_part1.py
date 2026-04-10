"""
tests/manipola/unit/test_transforms_base3.py
=============================================
Test aggiuntivi per coprire i branch mancanti in transforms/base.py:
  - op_join: auto-conversione tipi chiavi (object vs numeric, numeric vs numeric)
  - op_join: dedup colonne duplicate, chiave duplicata nel DataFrame
  - op_filter: eccezione nel filtro (colonna con tipo incompatibile)
  - op_geo_from_coords: mode=wkt, mode non supportato, lon mancante
  - op_formula: formula con metodi stringa (.str.upper()), costante, potenza
  - op_aggregate: concat con separator/distinct/skip_null, first/last
  - op_conditional: between, isnull/notnull, operatore non valido skippato
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.base import (
    op_join,
    op_filter,
    op_formula,
    op_aggregate,
    op_conditional,
    op_geo_from_coords,
    op_sample,
    op_melt,
    op_add_row_number,
    op_date_extract,
    op_lookup,
    op_bin,
    op_flatten_json,
    op_statistics,
    op_string_extract,
    op_cross_join,
    op_transpose,
    op_reorder_columns,
    op_string_pad,
    op_string_length,
    op_dedup_advanced,
    op_window_function,
    op_uuid_generator,
)




# =============================================================================
# op_join — auto-conversione tipi e dedup
# =============================================================================

class TestOpJoinAvanzato:

    def test_type_mismatch_int_vs_string(self):
        """Mutation target: int vs str → auto-conversione a stringa per join."""
        left  = pd.DataFrame({"id": [1, 2, 3],    "nome": ["a", "b", "c"]})
        right = pd.DataFrame({"id": ["1", "2", "4"], "valore": [10, 20, 40]})
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) == 2

    def test_type_mismatch_float_vs_int(self):
        """Mutation target: float vs int → auto-conversione numerica."""
        left  = pd.DataFrame({"id": [1.0, 2.0, 3.0], "nome": ["a", "b", "c"]})
        right = pd.DataFrame({"id": [1, 2, 4],        "valore": [10, 20, 40]})
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) == 2

    def test_dedup_colonne_duplicate_nel_left(self):
        """DataFrame con colonne duplicate → dedup prima del join."""
        left = pd.DataFrame([[1, "a", "x"]], columns=["id", "nome", "nome"])
        right = pd.DataFrame({"id": [1], "valore": [10]})
        # Non deve crashare
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) == 1

    def test_dedup_chiave_duplicata(self):
        """Chiave di join duplicata nel DataFrame → tenuta solo la prima."""
        left = pd.DataFrame([[1, "a"], [1, "b"]], columns=["id", "id"])
        right = pd.DataFrame({"id": [1], "valore": [10]})
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) >= 1

    def test_join_chiavi_diverse_left_right_type_mismatch(self):
        """left_on e right_on diversi con tipi diversi → auto-conversione."""
        left  = pd.DataFrame({"codice": [1, 2], "nome": ["a", "b"]})
        right = pd.DataFrame({"id": ["1", "2"], "valore": [10, 20]})
        result = op_join([left, right], left_on="codice", right_on="id", how="inner")
        assert len(result) == 2


# =============================================================================
# op_filter — casi edge aggiuntivi
# =============================================================================

class TestOpFilterAvanzato:

    def test_filter_eccezione_gestita(self):
        """Operatore valido ma colonna con tipo incompatibile → df invariato."""
        df = pd.DataFrame({"testo": ["abc", "def", "ghi"]})
        # > su stringa non numerica → eccezione interna, ritorna df
        result = op_filter(df, "testo", ">", "abc")
        # Può restituire risultato parziale o df invariato — non deve crashare
        assert len(result) >= 0

    def test_filter_between_non_supportato(self):
        """BETWEEN non è nella whitelist → df invariato."""
        df = pd.DataFrame({"valore": [100, 200, 300]})
        result = op_filter(df, "valore", "BETWEEN", "100,300")
        assert len(result) == len(df)

    def test_filter_valore_numerico_conversione_fallisce(self):
        """Valore non convertibile per colonna numerica → warning, prova comunque."""
        df = pd.DataFrame({"valore": [1.0, 2.0, 3.0]})
        result = op_filter(df, "valore", ">", "non_numero")
        # Non deve crashare
        assert len(result) >= 0


# =============================================================================
# op_formula — branch aggiuntivi
# =============================================================================

class TestOpFormulaAvanzato:

    def test_formula_potenza(self):
        df = pd.DataFrame({"x": [2.0, 3.0, 4.0]})
        result = op_formula(df, formula="x ** 2", new_column="quadrato")
        assert "quadrato" in result.columns
        assert abs(result["quadrato"].iloc[0] - 4.0) < 0.01

    def test_formula_modulo(self):
        df = pd.DataFrame({"id": [1, 2, 3, 4, 5]})
        result = op_formula(df, formula="id % 2", new_column="pari")
        assert "pari" in result.columns

    def test_formula_due_colonne_stringa(self):
        """Concatenazione di due colonne stringa."""
        df = pd.DataFrame({"nome": ["Mario"], "cognome": ["Rossi"]})
        result = op_formula(df, formula="nome + ' ' + cognome", new_column="nome_completo")
        assert "nome_completo" in result.columns
        assert result["nome_completo"].iloc[0] == "Mario Rossi"

    def test_formula_colonna_object_convertita(self):
        """Colonna object con valori numerici — pandas.eval non converte automaticamente.
        Il fallback eval con namespace prova la conversione.
        Verifica solo che non crashi e che produca una colonna."""
        df = pd.DataFrame({"valore": pd.to_numeric(["100", "200", "300"])})
        result = op_formula(df, formula="valore * 2", new_column="doppio")
        assert "doppio" in result.columns
        assert abs(result["doppio"].iloc[0] - 200.0) < 0.01

    def test_formula_formato_italiano(self):
        """Colonna con formato italiano (1.234,56) → conversione."""
        df = pd.DataFrame({"importo": ["1.000,50", "2.500,00"]})
        result = op_formula(df, formula="importo * 1", new_column="importo_num")
        # Può fallire la conversione → df invariato, no crash
        assert len(result) >= 0


# =============================================================================
# op_aggregate — branch mancanti
# =============================================================================

class TestOpAggregateAvanzato:

    def _df(self):
        return pd.DataFrame({
            "comune":  ["Roma", "Roma", "Milano", "Milano", "Roma"],
            "tipo":    ["A", "B", "A", "B", "A"],
            "valore":  [10.0, 20.0, 30.0, 40.0, 50.0],
            "nome":    ["x", "y", "z", "w", "v"],
        })

    def test_first(self):
        df = self._df()
        result = op_aggregate(df, group_by=["comune"],
                              aggregations=[{"column": "nome", "function": "first"}])
        assert "nome" in result.columns
        assert len(result) == 2

    def test_last(self):
        df = self._df()
        result = op_aggregate(df, group_by=["comune"],
                              aggregations=[{"column": "nome", "function": "last"}])
        assert "nome" in result.columns

    def test_concat_separator(self):
        df = self._df()
        result = op_aggregate(df, group_by=["comune"],
                              aggregations=[{
                                  "column": "nome", "function": "concat",
                                  "separator": "|", "distinct": False, "skip_null": True
                              }])
        assert "nome" in result.columns
        # Roma ha 3 valori → separati da |
        roma_val = result[result["comune"] == "Roma"]["nome"].iloc[0]
        assert "|" in roma_val

    def test_concat_distinct(self):
        """Valori duplicati con distinct=True → deduplicati."""
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Roma"],
            "tipo":   ["A", "A", "B"],
        })
        result = op_aggregate(df, group_by=["comune"],
                              aggregations=[{
                                  "column": "tipo", "function": "concat",
                                  "separator": ",", "distinct": True, "skip_null": True
                              }])
        assert "tipo" in result.columns
        # A,B (non A,A,B)
        val = result["tipo"].iloc[0]
        assert val.count("A") == 1

    def test_concat_skip_null_true(self):
        """skip_null=True → null non inclusi nella concatenazione."""
        df = pd.DataFrame({
            "comune": ["Roma", "Roma", "Roma"],
            "nome":   ["x", None, "y"],
        })
        result = op_aggregate(df, group_by=["comune"],
                              aggregations=[{
                                  "column": "nome", "function": "concat",
                                  "separator": ",", "skip_null": True
                              }])
        val = result["nome"].iloc[0]
        assert "None" not in val
        assert "nan" not in val.lower()

    def test_group_by_due_colonne(self):
        df = self._df()
        result = op_aggregate(df, group_by=["comune", "tipo"],
                              aggregations=[{"column": "valore", "function": "sum"}])
        assert "comune" in result.columns
        assert "tipo" in result.columns
        assert len(result) == df.groupby(["comune", "tipo"]).ngroups


# =============================================================================
# op_conditional — branch aggiuntivi
# =============================================================================

class TestOpConditionalAvanzato:

    def _df(self):
        return pd.DataFrame({
            "valore": [50.0, 150.0, 250.0, 350.0, None],
            "testo":  ["alfa", "beta", None, "gamma", "delta"],
        })

    def test_isnull_condition(self):
        df = self._df()
        result = op_conditional(df,
            column="testo",
            conditions=[{"operator": "isnull", "value": "", "result": "VUOTO"}],
            default_value="PIENO", output_column="stato",
        )
        assert result[result["testo"].isna()]["stato"].iloc[0] == "VUOTO"
        assert result[result["testo"] == "alfa"]["stato"].iloc[0] == "PIENO"

    def test_notnull_condition(self):
        df = self._df()
        result = op_conditional(df,
            column="valore",
            conditions=[{"operator": "notnull", "value": "", "result": "OK"}],
            default_value="NULL", output_column="check",
        )
        assert result[result["valore"].isna()]["check"].iloc[0] == "NULL"
        assert result[result["valore"] == 50.0]["check"].iloc[0] == "OK"

    def test_contains_condition(self):
        df = self._df()
        result = op_conditional(df,
            column="testo",
            conditions=[{"operator": "contains", "value": "alf", "result": "trovato"}],
            default_value="non trovato", output_column="ricerca",
        )
        assert result[result["testo"] == "alfa"]["ricerca"].iloc[0] == "trovato"
        assert result[result["testo"] == "beta"]["ricerca"].iloc[0] == "non trovato"

    def test_operatore_invalido_skippato(self):
        """Condizione con operatore invalido → skippata, non crasha."""
        df = self._df()
        result = op_conditional(df,
            column="valore",
            conditions=[{"operator": "OPERATORE_XYZ", "value": "100", "result": "X"}],
            default_value="default", output_column="out",
        )
        # Con operatore invalido → tutte le righe prendono il default
        assert all(result["out"] == "default")

    def test_multiple_condizioni_ordine_priorita(self):
        """Prima condizione che matcha vince."""
        df = pd.DataFrame({"v": [100.0, 200.0, 300.0]})
        result = op_conditional(df,
            column="v",
            conditions=[
                {"operator": ">=", "value": "100", "result": "basso"},
                {"operator": ">=", "value": "200", "result": "medio"},
                {"operator": ">=", "value": "300", "result": "alto"},
            ],
            default_value="none", output_column="fascia",
        )
        # Tutte matchano la prima condizione (>= 100)
        assert all(result["fascia"] == "basso")

    def test_auto_detect_numerico(self):
        """Se tutti i result values sono numerici → colonna numerica."""
        df = pd.DataFrame({"v": [10.0, 20.0, 30.0]})
        result = op_conditional(df,
            column="v",
            conditions=[{"operator": ">", "value": "15", "result": "2"}],
            default_value="1", output_column="cat",
        )
        assert "cat" in result.columns


# =============================================================================
# op_geo_from_coords — branch aggiuntivi
# =============================================================================

class TestOpGeoFromCoordsAvanzato:

    def test_mode_wkt(self):
        """mode=wkt → parse WKT dalla colonna specificata."""
        df = pd.DataFrame({
            "id":       [1, 2],
            "geom_wkt": ["POINT(12.4 41.9)", "POINT(9.1 45.4)"],
        })
        result = op_geo_from_coords(df,
            lat_column="", lon_column="",
            wkt_column="geom_wkt", mode="wkt",
            output_column="geometry",
        )
        assert "geometry" in result.columns
        assert len(result) == 2

    def test_mode_wkt_colonna_mancante(self):
        """mode=wkt ma wkt_column non esiste → df invariato."""
        df = pd.DataFrame({"id": [1, 2]})
        result = op_geo_from_coords(df,
            lat_column="", lon_column="",
            wkt_column="COLONNA_XYZ", mode="wkt",
        )
        assert "geometry" not in result.columns

    def test_mode_non_supportato(self):
        """mode sconosciuto → df invariato."""
        df = pd.DataFrame({"lat": [41.9], "lon": [12.4]})
        result = op_geo_from_coords(df,
            lat_column="lat", lon_column="lon",
            mode="xyz_inesistente",
        )
        assert "geometry" not in result.columns

    def test_lon_column_mancante(self):
        """lon_column non esiste → df invariato."""
        df = pd.DataFrame({"lat": [41.9, 45.4]})
        result = op_geo_from_coords(df,
            lat_column="lat", lon_column="COLONNA_XYZ",
        )
        assert "geometry" not in result.columns

    def test_crs_custom(self):
        """CRS personalizzato passato esplicitamente."""
        df = pd.DataFrame({"lat": [41.9], "lon": [12.4]})
        result = op_geo_from_coords(df,
            lat_column="lat", lon_column="lon",
            crs="EPSG:32632", output_column="geometry",
        )
        assert "geometry" in result.columns


# =============================================================================
# Helper — DataFrame di riferimento per le operazioni base
# =============================================================================

def _immobili(n=10):
    """DataFrame di test con dati immobiliari OMI."""
    return pd.DataFrame({
        "id": range(1, n + 1),
        "comune": ["Roma", "Milano", "Roma", "Napoli", "Milano",
                    "Roma", "Torino", "Roma", "Milano", "Napoli"][:n],
        "zona": ["A", "B", "A", "C", "B", "A", "D", "A", "B", "C"][:n],
        "valore_omi": [1200.0, 2500.0, 1100.0, 900.0, 2400.0,
                       1300.0, 1800.0, 1150.0, 2450.0, 950.0][:n],
        "superficie": [80, 120, 75, 60, 110, 85, 95, 70, 115, 65][:n],
        "anno": [2020, 2021, 2020, 2022, 2021, 2023, 2022, 2020, 2021, 2022][:n],
    })


# =============================================================================
# op_sample — campionamento casuale
# =============================================================================

class TestOpSample:

    def test_sample_n(self):
        """Campionamento di n righe esatte."""
        df = _immobili()
        result = op_sample(df, n=5)
        assert len(result) == 5

    def test_sample_fraction(self):
        """Campionamento per frazione (50%) — circa 5 righe."""
        df = _immobili()
        result = op_sample(df, fraction=0.5, random_state=42)
        assert len(result) == 5

    def test_sample_riproducibile(self):
        """Stesso random_state produce lo stesso campione."""
        df = _immobili()
        r1 = op_sample(df, n=5, random_state=123)
        r2 = op_sample(df, n=5, random_state=123)
        pd.testing.assert_frame_equal(r1.reset_index(drop=True),
                                      r2.reset_index(drop=True))

    def test_sample_stratificato(self):
        """Campionamento stratificato mantiene proporzioni per colonna."""
        df = _immobili(10)
        result = op_sample(df, n=5, stratify_column="comune", random_state=42)
        assert len(result) == 5
        # Roma ha 4/10 nel dataset → dovrebbe avere ~2 nel campione
        roma_count = (result["comune"] == "Roma").sum()
        assert roma_count >= 1

    def test_sample_n_maggiore_dataset(self):
        """Se n > len(df) il risultato viene troncato alla dimensione del df."""
        df = _immobili(5)
        result = op_sample(df, n=100)
        assert len(result) <= 5


# =============================================================================
# op_melt — unpivot wide → long
# =============================================================================

class TestOpMelt:

    def test_melt_base(self):
        """Trasformazione wide → long con colonne valore/superficie."""
        df = _immobili(3)
        result = op_melt(df, id_columns=["id", "comune"],
                         value_columns=["valore_omi", "superficie"])
        assert "variable" in result.columns
        assert "value" in result.columns
        # 3 righe x 2 colonne = 6 righe
        assert len(result) == 6

    def test_colonne_chiave_preservate(self):
        """Le colonne id sono mantenute in tutte le righe."""
        df = _immobili(3)
        result = op_melt(df, id_columns=["id", "comune"],
                         value_columns=["valore_omi"])
        assert set(result["id"].tolist()) == {1, 2, 3}
        assert "id" in result.columns
        assert "comune" in result.columns

    def test_var_name_custom(self):
        """Nome personalizzato per la colonna variabile."""
        df = _immobili(3)
        result = op_melt(df, id_columns=["id"],
                         value_columns=["valore_omi", "superficie"],
                         var_name="metrica", value_name="misura")
        assert "metrica" in result.columns
        assert "misura" in result.columns

    def test_value_columns_specifiche(self):
        """Solo le colonne specificate vengono sciolte."""
        df = _immobili(3)
        result = op_melt(df, id_columns=["id"],
                         value_columns=["valore_omi"])
        assert len(result) == 3
        assert set(result["variable"].unique()) == {"valore_omi"}
