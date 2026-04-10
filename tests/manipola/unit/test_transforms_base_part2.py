"""
tests/manipola/unit/test_transforms_base.py
============================================
Unit test diretti sulle funzioni op_* di transforms/base.py.

NON richiede backend, API o Celery.
Ogni op_* è una funzione pura DataFrame → DataFrame — testabile direttamente.

Obiettivi coverage:
  - Ogni branch di ogni op_*
  - Mutation score: assert sui valori esatti (count righe, nomi colonne, valori)
  - Edge case: colonne inesistenti, valori vuoti, DataFrame vuoto
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.base import (
    op_distinct,
    op_filter,
    op_formula,
    op_aggregate,
    op_sort,
    op_rename,
    op_drop_columns,
    op_pivot,
    op_conditional,
    op_concat_columns,
    op_type_cast,
    op_fill_na,
    op_replace,
    op_md5_hash,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def _immobili(n=10):
    """DataFrame sintetico simile a csv_immobili."""
    return pd.DataFrame({
        "id":          range(1, n+1),
        "comune":      ["Roma", "Milano", "Roma", "Napoli", "Milano",
                        "Roma", "Torino", "Roma", "Milano", "Napoli"][:n],
        "zona":        ["A", "B", "A", "C", "B", "A", "D", "A", "B", "C"][:n],
        "tipo":        ["Residenziale", "Commerciale", "Residenziale", "Residenziale",
                        "Commerciale", "Residenziale", "Commerciale", "Residenziale",
                        "Residenziale", "Commerciale"][:n],
        "valore_omi":  [1200.0, 2500.0, 1100.0, 900.0, 2400.0,
                        1300.0, 1800.0, 1150.0, 2450.0, 950.0][:n],
        "superficie":  [80, 120, 75, 60, 110, 85, 95, 70, 115, 65][:n],
        "anno":        [2020, 2021, 2020, 2022, 2021, 2023, 2022, 2020, 2021, 2022][:n],
    })


def _filter_df():
    """DataFrame per test filter."""
    return pd.DataFrame({
        "regione":  ["Lazio", "Lombardia", "Lazio", "Campania", "Lombardia"],
        "tipo_uso": ["Residenziale", "Commerciale", "Residenziale", "Residenziale", "Commerciale"],
        "valore":   [100.0, 200.0, 150.0, 80.0, 300.0],
        "anno":     [2020, 2021, 2020, 2022, 2021],
        "attivo":   [True, True, False, True, False],
    })




# =============================================================================
# op_pivot
# =============================================================================

class TestOpPivot:

    def _pivot_df(self):
        return pd.DataFrame({
            "anno":      [2020, 2020, 2021, 2021, 2022, 2022],
            "trimestre": ["Q1", "Q2", "Q1", "Q2", "Q1", "Q2"],
            "valore":    [100.0, 200.0, 150.0, 250.0, 120.0, 220.0],
            "comune":    ["Roma", "Roma", "Roma", "Roma", "Roma", "Roma"],
        })

    def test_pivot_base(self):
        df = self._pivot_df()
        result = op_pivot(df,
            index_col="anno",
            pivot_col="trimestre",
            value_col="valore",
            aggr_func="first",
        )
        assert "Q1" in result.columns or any("Q1" in str(c) for c in result.columns)
        assert len(result) == 3   # 3 anni

    def test_parametri_mancanti_restituisce_invariato(self):
        df = self._pivot_df()
        result = op_pivot(df, index_col="", pivot_col="", value_col="", aggr_func="first")
        assert len(result) == len(df)

    def test_colonne_inesistenti_restituisce_invariato(self):
        df = self._pivot_df()
        result = op_pivot(df,
            index_col="COLONNA_XYZ",
            pivot_col="trimestre",
            value_col="valore",
            aggr_func="first",
        )
        assert len(result) == len(df)

    def test_aggr_func_sum(self):
        df = self._pivot_df()
        result = op_pivot(df,
            index_col="anno",
            pivot_col="trimestre",
            value_col="valore",
            aggr_func="sum",
        )
        assert len(result) == 3

    def test_aggr_func_non_supportata_fallback(self):
        """Aggr func non supportata → fallback a first, no crash."""
        df = self._pivot_df()
        result = op_pivot(df,
            index_col="anno",
            pivot_col="trimestre",
            value_col="valore",
            aggr_func="FUNZIONE_XYZ",
        )
        assert len(result) > 0


# =============================================================================
# op_conditional
# =============================================================================

class TestOpConditional:

    def _cond_df(self):
        return pd.DataFrame({
            "tipo_uso": ["Residenziale", "Commerciale", "Residenziale", "Industriale"],
            "valore":   [100.0, 500.0, 150.0, 300.0],
            "anno":     [2020, 2021, 2022, 2023],
        })

    def test_condizione_singola_con_default(self):
        df = self._cond_df()
        result = op_conditional(df,
            column="tipo_uso",
            conditions=[{"operator": "==", "value": "Residenziale", "result": "RES"}],
            default_value="ALTRO",
            output_column="cat",
        )
        assert "cat" in result.columns
        assert result[result["tipo_uso"] == "Residenziale"]["cat"].iloc[0] == "RES"
        assert result[result["tipo_uso"] == "Commerciale"]["cat"].iloc[0] == "ALTRO"

    def test_output_column_custom(self):
        df = self._cond_df()
        result = op_conditional(df,
            column="valore",
            conditions=[{"operator": ">", "value": "200", "result": "alto"}],
            default_value="basso",
            output_column="fascia_prezzo",
        )
        assert "fascia_prezzo" in result.columns

    def test_column_inesistente_restituisce_invariato(self):
        df = self._cond_df()
        result = op_conditional(df,
            column="COLONNA_XYZ",
            conditions=[{"operator": "==", "value": "x", "result": "y"}],
            default_value="z",
            output_column="out",
        )
        assert "out" not in result.columns
        assert len(result) == len(df)

    def test_conditions_vuote_restituisce_invariato(self):
        df = self._cond_df()
        result = op_conditional(df,
            column="tipo_uso",
            conditions=[],
            default_value="default",
            output_column="out",
        )
        assert "out" not in result.columns

    def test_priorita_prima_condizione_vince(self):
        """Mutation target: la prima condizione che matcha vince."""
        df = pd.DataFrame({"v": [100.0, 200.0, 300.0]})
        result = op_conditional(df,
            column="v",
            conditions=[
                {"operator": ">",  "value": "50",  "result": "grande"},
                {"operator": ">",  "value": "150", "result": "molto_grande"},
            ],
            default_value="piccolo",
            output_column="cat",
        )
        # 200 matcha sia "grande" che "molto_grande" — deve prendere "grande"
        assert result[result["v"] == 200.0]["cat"].iloc[0] == "grande"

    def test_between(self):
        df = pd.DataFrame({"valore": [50.0, 150.0, 250.0, 350.0]})
        result = op_conditional(df,
            column="valore",
            conditions=[{"operator": "between", "value": "100,300", "result": "medio"}],
            default_value="fuori",
            output_column="fascia",
        )
        assert result[result["valore"] == 150.0]["fascia"].iloc[0] == "medio"
        assert result[result["valore"] == 350.0]["fascia"].iloc[0] == "fuori"


# =============================================================================
# op_concat_columns
# =============================================================================

class TestOpConcatColumns:

    def test_concat_due_colonne(self):
        df = pd.DataFrame({"nome": ["Mario", "Luigi"], "cognome": ["Rossi", "Bianchi"]})
        result = op_concat_columns(df,
            columns=["nome", "cognome"],
            output_column="nome_completo",
            separator=" ",
        )
        assert "nome_completo" in result.columns
        assert result["nome_completo"].iloc[0] == "Mario Rossi"

    def test_separatore_custom(self):
        df = pd.DataFrame({"a": ["X"], "b": ["Y"]})
        result = op_concat_columns(df, columns=["a", "b"], output_column="c", separator="-")
        assert result["c"].iloc[0] == "X-Y"

    def test_colonne_inesistenti_skip(self):
        """Colonne non esistenti → skippate senza crash."""
        df = pd.DataFrame({"nome": ["Mario"]})
        result = op_concat_columns(df,
            columns=["nome", "COLONNA_XYZ"],
            output_column="out",
            separator=" ",
        )
        assert "out" in result.columns


# =============================================================================
# op_type_cast
# =============================================================================

class TestOpTypeCast:

    def test_cast_str_to_int(self):
        df = pd.DataFrame({"valore": ["1", "2", "3"]})
        result = op_type_cast(df, column="valore", target_type="int")
        assert pd.api.types.is_integer_dtype(result["valore"])

    def test_cast_str_to_float(self):
        df = pd.DataFrame({"valore": ["1.5", "2.3", "3.7"]})
        result = op_type_cast(df, column="valore", target_type="float")
        assert pd.api.types.is_float_dtype(result["valore"])

    def test_cast_int_to_str(self):
        df = pd.DataFrame({"codice": [1, 2, 3]})
        result = op_type_cast(df, column="codice", target_type="str")
        # pandas 2.x può usare StringDtype invece di object — verifica che sia string-like
        dtype = str(result["codice"].dtype)
        assert "str" in dtype.lower() or "object" in dtype.lower() or "string" in dtype.lower()

    def test_colonna_inesistente_restituisce_invariato(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = op_type_cast(df, column="COLONNA_XYZ", target_type="str")
        assert list(result.columns) == ["a"]

    def test_cast_fallisce_gracefully(self):
        """Cast impossibile → df invariato, no crash."""
        df = pd.DataFrame({"valore": ["abc", "def"]})
        result = op_type_cast(df, column="valore", target_type="int")
        assert len(result) == len(df)


# =============================================================================
# op_fill_na
# =============================================================================

class TestOpFillNa:

    def test_fill_con_valore(self):
        df = pd.DataFrame({"a": [1.0, None, 3.0], "b": ["x", None, "z"]})
        result = op_fill_na(df, column="a", value=0.0)
        assert result["a"].isna().sum() == 0
        assert result["a"].iloc[1] == 0.0

    def test_fill_stringa(self):
        df = pd.DataFrame({"nome": ["Mario", None, "Luigi"]})
        result = op_fill_na(df, column="nome", value="N/D")
        assert result["nome"].iloc[1] == "N/D"

    def test_colonna_inesistente_restituisce_invariato(self):
        df = pd.DataFrame({"a": [1, None, 3]})
        result = op_fill_na(df, column="XYZ", value=0)
        assert list(result.columns) == ["a"]

    def test_nessun_null_invariato(self):
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
        result = op_fill_na(df, column="a", value=0.0)
        assert list(result["a"]) == [1.0, 2.0, 3.0]


# =============================================================================
# op_replace
# =============================================================================

class TestOpReplace:

    def test_replace_valore(self):
        df = pd.DataFrame({"tipo": ["A", "B", "A", "C"]})
        result = op_replace(df, column="tipo", old_value="A", new_value="X")
        assert list(result["tipo"]) == ["X", "B", "X", "C"]

    def test_valore_non_presente_invariato(self):
        df = pd.DataFrame({"tipo": ["A", "B", "C"]})
        result = op_replace(df, column="tipo", old_value="Z", new_value="X")
        assert list(result["tipo"]) == ["A", "B", "C"]

    def test_colonna_inesistente_restituisce_invariato(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = op_replace(df, column="XYZ", old_value="a", new_value="b")
        assert list(result.columns) == ["a"]


# =============================================================================
# op_md5_hash
# =============================================================================

class TestOpMd5Hash:

    def test_hash_colonna(self):
        df = pd.DataFrame({"cf": ["RSSMRA80A01H501Z", "BNCGPP75B12F205X"]})
        result = op_md5_hash(df, columns=["cf"], output_column="cf_hash")
        assert "cf_hash" in result.columns
        # Hash MD5 ha 32 caratteri esadecimali
        assert len(result["cf_hash"].iloc[0]) == 32
        # Due valori diversi → hash diversi
        assert result["cf_hash"].iloc[0] != result["cf_hash"].iloc[1]

    def test_hash_deterministico(self):
        """Stesso valore → stesso hash (deterministico)."""
        df = pd.DataFrame({"cf": ["RSSMRA80A01H501Z", "RSSMRA80A01H501Z"]})
        result = op_md5_hash(df, columns=["cf"], output_column="cf_hash")
        assert result["cf_hash"].iloc[0] == result["cf_hash"].iloc[1]

    def test_colonna_inesistente_restituisce_invariato(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = op_md5_hash(df, columns=["XYZ"], output_column="hash")
        assert len(result) == len(df)
