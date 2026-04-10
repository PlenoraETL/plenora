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
# op_distinct
# =============================================================================

class TestOpDistinct:

    def test_rimuove_duplicati_su_tutte_le_colonne(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
        result = op_distinct(df)
        assert len(result) == 2

    def test_subset_colonne(self):
        """Duplicati solo su 'comune' → righe uniche per comune."""
        df = _immobili(10)
        result = op_distinct(df, subset=["comune"])
        comuni_unici = df["comune"].nunique()
        assert len(result) == comuni_unici

    def test_keep_first(self):
        """keep=first → primo duplicato preservato."""
        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        result = op_distinct(df, subset=["a"], keep="first")
        assert result[result["a"] == 1]["b"].iloc[0] == 10

    def test_keep_last(self):
        """Mutation target: keep=last → ultimo duplicato preservato."""
        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        result = op_distinct(df, subset=["a"], keep="last")
        assert result[result["a"] == 1]["b"].iloc[0] == 20

    def test_keep_false_rimuove_tutti(self):
        """keep='false' → rimuove tutti i duplicati incluso l'originale."""
        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})
        result = op_distinct(df, subset=["a"], keep="false")
        assert len(result) == 1
        assert result["a"].iloc[0] == 2

    def test_subset_colonna_inesistente_ignorata(self):
        """Colonna non esistente nel subset → ignorata, nessun crash."""
        df = _immobili(5)
        result = op_distinct(df, subset=["comune", "COLONNA_INESISTENTE"])
        assert len(result) <= len(df)

    def test_df_vuoto_restituito_invariato(self):
        df = pd.DataFrame({"a": [], "b": []})
        result = op_distinct(df)
        assert len(result) == 0

    def test_nessun_duplicato_invariato(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        result = op_distinct(df)
        assert len(result) == 3

    def test_indici_resettati(self):
        """Mutation target: indici devono essere 0..N-1 dopo distinct."""
        df = pd.DataFrame({"a": [1, 1, 2, 3]})
        result = op_distinct(df)
        assert list(result.index) == list(range(len(result)))


# =============================================================================
# op_filter
# =============================================================================

class TestOpFilter:

    def test_colonna_inesistente_restituisce_invariato(self):
        df = _filter_df()
        result = op_filter(df, "COLONNA_XYZ", "==", "Lazio")
        assert len(result) == len(df)

    def test_operatore_non_supportato_restituisce_invariato(self):
        df = _filter_df()
        result = op_filter(df, "regione", "BETWEEN", "Lazio")
        assert len(result) == len(df)

    def test_eq_stringa(self):
        df = _filter_df()
        result = op_filter(df, "regione", "==", "Lazio")
        assert len(result) == 2
        assert all(result["regione"] == "Lazio")

    def test_neq_stringa(self):
        df = _filter_df()
        result = op_filter(df, "regione", "!=", "Lazio")
        assert len(result) == 3
        assert "Lazio" not in result["regione"].values

    def test_gt_numerico(self):
        """Mutation target: > deve escludere il valore esatto."""
        df = _filter_df()
        result = op_filter(df, "valore", ">", "150")
        assert all(result["valore"] > 150)
        assert 150.0 not in result["valore"].values

    def test_gte_numerico(self):
        """Mutation target: >= deve includere il valore esatto."""
        df = _filter_df()
        result = op_filter(df, "valore", ">=", "150")
        assert all(result["valore"] >= 150)
        assert 150.0 in result["valore"].values

    def test_lt_numerico(self):
        df = _filter_df()
        result = op_filter(df, "valore", "<", "150")
        assert all(result["valore"] < 150)

    def test_lte_numerico(self):
        df = _filter_df()
        result = op_filter(df, "valore", "<=", "150")
        assert all(result["valore"] <= 150)

    def test_contains_case_insensitive(self):
        df = _filter_df()
        result = op_filter(df, "tipo_uso", "contains", "residen")
        assert len(result) == 3
        assert all("Residenziale" in v for v in result["tipo_uso"])

    def test_startswith(self):
        df = _filter_df()
        result = op_filter(df, "regione", "startswith", "Lom")
        assert len(result) == 2
        assert all(r.startswith("Lom") for r in result["regione"])

    def test_endswith(self):
        """Mutation target: endswith 'Commerciale' → 2 righe."""
        df = _filter_df()
        result = op_filter(df, "tipo_uso", "endswith", "Commerciale")
        assert len(result) == 2
        assert all(r.endswith("Commerciale") for r in result["tipo_uso"])

    def test_isnull(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", "z"]})
        result = op_filter(df, "a", "isnull")
        assert len(result) == 1
        assert pd.isna(result["a"].iloc[0])

    def test_notnull(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", "z"]})
        result = op_filter(df, "a", "notnull")
        assert len(result) == 2
        assert not result["a"].isna().any()

    def test_filtro_booleano(self):
        df = _filter_df()
        result = op_filter(df, "attivo", "==", True)
        assert len(result) == 3

    def test_risultato_vuoto_non_crasha(self):
        df = _filter_df()
        result = op_filter(df, "valore", ">", "999999")
        assert len(result) == 0

    def test_indici_resettati(self):
        df = _filter_df()
        result = op_filter(df, "regione", "==", "Lazio")
        assert list(result.index) == list(range(len(result)))


# =============================================================================
# op_sort
# =============================================================================

class TestOpSort:

    def test_colonne_vuote_restituisce_invariato(self):
        df = _immobili(5)
        result = op_sort(df, [])
        assert len(result) == len(df)

    def test_colonne_inesistenti_restituisce_invariato(self):
        df = _immobili(5)
        result = op_sort(df, ["COLONNA_XYZ"])
        assert len(result) == len(df)

    def test_ascending_numerico(self):
        """Mutation target: primo elemento deve essere il minore."""
        df = _immobili(10)
        result = op_sort(df, ["valore_omi"], ascending=True)
        assert result["valore_omi"].iloc[0] == df["valore_omi"].min()

    def test_descending_numerico(self):
        """Mutation target: primo elemento deve essere il maggiore."""
        df = _immobili(10)
        result = op_sort(df, ["valore_omi"], ascending=False)
        assert result["valore_omi"].iloc[0] == df["valore_omi"].max()

    def test_sort_stringa_alfabetico(self):
        df = _immobili(10)
        result = op_sort(df, ["comune"], ascending=True)
        comuni = list(result["comune"])
        assert comuni == sorted(comuni)

    def test_sort_multicolonna(self):
        """Sort su comune (asc) poi valore_omi (desc)."""
        df = _immobili(10)
        result = op_sort(df, ["comune", "valore_omi"], ascending=True)
        assert len(result) == len(df)
        # Il comune del primo deve essere <= comune del secondo
        assert result["comune"].iloc[0] <= result["comune"].iloc[1]

    def test_row_count_invariato(self):
        df = _immobili(10)
        result = op_sort(df, ["anno"])
        assert len(result) == len(df)

    def test_indici_resettati(self):
        df = _immobili(10)
        result = op_sort(df, ["valore_omi"])
        assert list(result.index) == list(range(len(result)))


# =============================================================================
# op_rename
# =============================================================================

class TestOpRename:

    def test_rename_singola_retrocompat(self):
        df = _immobili(3)
        result = op_rename(df, old_name="comune", new_name="citta")
        assert "citta" in result.columns
        assert "comune" not in result.columns

    def test_rename_lista(self):
        df = _immobili(3)
        result = op_rename(df, renames=[
            {"old_name": "comune", "new_name": "citta"},
            {"old_name": "anno",   "new_name": "year"},
        ])
        assert "citta" in result.columns
        assert "year" in result.columns
        assert "comune" not in result.columns
        assert "anno" not in result.columns

    def test_colonna_inesistente_skippata(self):
        df = _immobili(3)
        result = op_rename(df, old_name="COLONNA_XYZ", new_name="nuova")
        assert "nuova" not in result.columns
        assert list(result.columns) == list(df.columns)

    def test_renames_vuota_restituisce_invariato(self):
        df = _immobili(3)
        result = op_rename(df, renames=[])
        assert list(result.columns) == list(df.columns)

    def test_nessun_parametro_restituisce_invariato(self):
        df = _immobili(3)
        result = op_rename(df)
        assert list(result.columns) == list(df.columns)

    def test_dati_preservati_dopo_rename(self):
        df = _immobili(3)
        result = op_rename(df, old_name="comune", new_name="citta")
        assert list(result["citta"]) == list(df["comune"])


# =============================================================================
# op_drop_columns
# =============================================================================

class TestOpDropColumns:

    def test_drop_colonne_esistenti(self):
        df = _immobili(5)
        result = op_drop_columns(df, columns=["zona", "tipo"])
        assert "zona" not in result.columns
        assert "tipo" not in result.columns
        assert "id" in result.columns

    def test_colonna_inesistente_skippata(self):
        df = _immobili(5)
        result = op_drop_columns(df, columns=["COLONNA_XYZ", "comune"])
        assert "comune" not in result.columns
        assert "id" in result.columns

    def test_columns_vuota_restituisce_invariato(self):
        df = _immobili(5)
        result = op_drop_columns(df, columns=[])
        assert list(result.columns) == list(df.columns)

    def test_tutte_inesistenti_restituisce_invariato(self):
        df = _immobili(5)
        result = op_drop_columns(df, columns=["XYZ", "ABC"])
        assert list(result.columns) == list(df.columns)

    def test_row_count_invariato(self):
        df = _immobili(5)
        result = op_drop_columns(df, columns=["zona"])
        assert len(result) == len(df)


# =============================================================================
# op_formula
# =============================================================================

class TestOpFormula:

    def test_formula_aritmetica(self):
        df = _immobili(5)
        result = op_formula(df, formula="valore_omi * superficie", new_column="totale")
        assert "totale" in result.columns
        expected = df["valore_omi"].iloc[0] * df["superficie"].iloc[0]
        assert abs(result["totale"].iloc[0] - expected) < 0.01

    def test_formula_vuota_restituisce_invariato(self):
        df = _immobili(5)
        result = op_formula(df, formula="", new_column="x")
        assert "x" not in result.columns
        assert len(result) == len(df)

    def test_formula_con_costante(self):
        df = _immobili(5)
        result = op_formula(df, formula="valore_omi + 100", new_column="valore_più")
        assert "valore_più" in result.columns
        assert abs(result["valore_più"].iloc[0] - (df["valore_omi"].iloc[0] + 100)) < 0.01

    def test_formula_stringa(self):
        """Formula con stringa → usa astype(str)."""
        df = _immobili(5)
        result = op_formula(df, formula="'Comune: ' + comune", new_column="etichetta")
        assert "etichetta" in result.columns
        assert result["etichetta"].iloc[0].startswith("Comune: ")

    def test_formula_errore_restituisce_invariato(self):
        """Formula invalida → return df invariato, no crash."""
        df = _immobili(5)
        result = op_formula(df, formula="colonna_inesistente * 2", new_column="x")
        assert len(result) == len(df)

    def test_new_column_custom(self):
        df = _immobili(5)
        result = op_formula(df, formula="superficie * 2", new_column="superficie_doppia")
        assert "superficie_doppia" in result.columns

    def test_formula_divisione(self):
        df = _immobili(5)
        result = op_formula(df, formula="valore_omi / superficie", new_column="prezzo_mq")
        assert "prezzo_mq" in result.columns
        expected = df["valore_omi"].iloc[0] / df["superficie"].iloc[0]
        assert abs(result["prezzo_mq"].iloc[0] - expected) < 0.01


# =============================================================================
# op_aggregate
# =============================================================================

class TestOpAggregate:

    def test_group_by_vuoto_restituisce_invariato(self):
        df = _immobili(10)
        result = op_aggregate(df, group_by=[], aggregations=[])
        assert len(result) == len(df)

    def test_group_by_colonna_inesistente_restituisce_invariato(self):
        df = _immobili(10)
        result = op_aggregate(df, group_by=["COLONNA_XYZ"], aggregations=[])
        assert len(result) == len(df)

    def test_count_per_gruppo(self):
        df = _immobili(10)
        result = op_aggregate(df,
            group_by=["comune"],
            aggregations=[{"column": "id", "function": "count"}]
        )
        assert "comune" in result.columns
        # Il backend produce "id" come nome colonna (no alias)
        assert "id" in result.columns
        assert len(result) == df["comune"].nunique()

    def test_sum(self):
        df = _immobili(10)
        result = op_aggregate(df,
            group_by=["comune"],
            aggregations=[{"column": "superficie", "function": "sum"}]
        )
        # Il backend produce "superficie" come nome colonna (no alias)
        assert "superficie" in result.columns
        # Verifica somma per Roma
        roma_sum = df[df["comune"] == "Roma"]["superficie"].sum()
        result_roma = result[result["comune"] == "Roma"]["superficie"].iloc[0]
        assert abs(result_roma - roma_sum) < 0.01

    def test_mean(self):
        df = _immobili(10)
        result = op_aggregate(df,
            group_by=["comune"],
            aggregations=[{"column": "valore_omi", "function": "mean"}]
        )
        # Il backend produce "valore_omi" come nome colonna (no alias)
        assert "valore_omi" in result.columns

    def test_min_max(self):
        df = _immobili(10)
        result = op_aggregate(df,
            group_by=["comune"],
            aggregations=[
                {"column": "valore_omi", "function": "min"},
                {"column": "valore_omi", "function": "max"},
            ]
        )
        # MultiIndex flatten: produce "valore_omi_min" e "valore_omi_max"
        assert "valore_omi_min" in result.columns
        assert "valore_omi_max" in result.columns
        assert all(result["valore_omi_min"] <= result["valore_omi_max"])

    def test_aggregations_vuote_solo_count(self):
        """aggregations vuote → solo count(*) per gruppo."""
        df = _immobili(10)
        result = op_aggregate(df, group_by=["comune"], aggregations=[])
        assert len(result) == df["comune"].nunique()

    def test_colonna_aggregazione_inesistente_skippata(self):
        """Colonna non esistente nell'aggregazione → skippata silenziosamente."""
        df = _immobili(10)
        result = op_aggregate(df,
            group_by=["comune"],
            aggregations=[{"column": "COLONNA_XYZ", "function": "sum", "alias": "x"}]
        )
        assert len(result) == df["comune"].nunique()
