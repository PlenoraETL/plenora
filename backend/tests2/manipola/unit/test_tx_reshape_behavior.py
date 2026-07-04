"""
Behavioral tests per il cluster RESHAPE.

Copre le 4 operazioni pubbliche di
``app.core.manipola.transforms.reshape``:
    - op_pivot    (righe -> colonne)
    - op_table_diff (confronto 2 tabelle)
    - op_melt     (colonne -> righe / unpivot)
    - op_transpose (scambio righe/colonne)

Test comportamentali: input pandas.DataFrame minimi e realistici,
verifica forma/contenuto dell'output e dei rami di guardia
(parametri mancanti, colonne assenti, mapping, collisioni, ecc.).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.manipola.transforms.reshape import (
    op_melt,
    op_pivot,
    op_table_diff,
    op_transpose,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# ---------------------------------------------------------------------------
# op_pivot
# ---------------------------------------------------------------------------


def _pivot_df():
    return pd.DataFrame(
        {
            "immobile": ["A", "A", "B"],
            "tipo": ["canone", "rendita", "canone"],
            "importo": [1000, 500, 2000],
        }
    )


def test_pivot_happy_path_spreads_rows_to_columns():
    df = _pivot_df()
    result = op_pivot(
        df,
        index_col="immobile",
        pivot_col="tipo",
        value_col="importo",
        aggr_func="max",
    )
    # immobile diventa colonna PK + una colonna per ogni valore di tipo
    assert "immobile" in result.columns
    assert "canone" in result.columns
    assert "rendita" in result.columns
    assert len(result) == 2  # A, B
    row_a = result[result["immobile"] == "A"].iloc[0]
    assert row_a["canone"] == 1000
    assert row_a["rendita"] == 500
    row_b = result[result["immobile"] == "B"].iloc[0]
    assert row_b["canone"] == 2000
    # B non ha rendita -> NaN
    assert pd.isna(row_b["rendita"])


def test_pivot_with_mapping_renames_and_filters_columns():
    df = _pivot_df()
    result = op_pivot(
        df,
        index_col="immobile",
        pivot_col="tipo",
        value_col="importo",
        aggr_func="max",
        mapping={"canone": "val_canone", "rendita": "val_rendita"},
    )
    assert set(result.columns) == {"immobile", "val_canone", "val_rendita"}
    row_a = result[result["immobile"] == "A"].iloc[0]
    assert row_a["val_canone"] == 1000
    assert row_a["val_rendita"] == 500


def test_pivot_mapping_drops_values_not_in_mapping():
    df = pd.DataFrame(
        {
            "immobile": ["A", "A", "A"],
            "tipo": ["canone", "rendita", "extra"],
            "importo": [1000, 500, 999],
        }
    )
    result = op_pivot(
        df,
        index_col="immobile",
        pivot_col="tipo",
        value_col="importo",
        mapping={"canone": "val_canone"},
    )
    # solo la colonna mappata + index, "extra" e "rendita" scartati
    assert set(result.columns) == {"immobile", "val_canone"}
    assert result.iloc[0]["val_canone"] == 1000


def test_pivot_multi_index_col_comma_separated():
    df = pd.DataFrame(
        {
            "edificio": ["E1", "E1", "E1"],
            "immobile": ["A", "A", "B"],
            "tipo": ["canone", "rendita", "canone"],
            "importo": [1000, 500, 2000],
        }
    )
    result = op_pivot(
        df,
        index_col="edificio, immobile",
        pivot_col="tipo",
        value_col="importo",
    )
    assert "edificio" in result.columns
    assert "immobile" in result.columns
    assert len(result) == 2  # (E1,A) e (E1,B)


def test_pivot_aggr_concat_joins_values():
    df = pd.DataFrame(
        {
            "k": ["A", "A"],
            "tipo": ["t", "t"],
            "val": ["x", "y"],
        }
    )
    result = op_pivot(
        df,
        index_col="k",
        pivot_col="tipo",
        value_col="val",
        aggr_func="concat",
    )
    cell = str(result.iloc[0]["t"])
    assert "x" in cell and "y" in cell


def test_pivot_aggr_sum_aggregates_duplicates():
    df = pd.DataFrame(
        {
            "k": ["A", "A"],
            "tipo": ["t", "t"],
            "val": [10, 5],
        }
    )
    result = op_pivot(
        df,
        index_col="k",
        pivot_col="tipo",
        value_col="val",
        aggr_func="sum",
    )
    assert result.iloc[0]["t"] == 15


def test_pivot_unknown_aggr_func_falls_back_to_first():
    df = _pivot_df()
    result = op_pivot(
        df,
        index_col="immobile",
        pivot_col="tipo",
        value_col="importo",
        aggr_func="bogus_func",
    )
    # non solleva: usa 'first' come fallback, output prodotto correttamente
    assert "canone" in result.columns
    assert len(result) == 2


def test_pivot_missing_required_param_returns_input_unchanged():
    df = _pivot_df()
    result = op_pivot(df, index_col="", pivot_col="tipo", value_col="importo")
    # parametro obbligatorio vuoto -> ritorna df invariato
    pd.testing.assert_frame_equal(result, df)


def test_pivot_missing_column_returns_input_unchanged():
    df = _pivot_df()
    result = op_pivot(
        df, index_col="immobile", pivot_col="NON_ESISTE", value_col="importo"
    )
    pd.testing.assert_frame_equal(result, df)


def test_pivot_mapping_no_match_returns_input_unchanged():
    df = _pivot_df()
    result = op_pivot(
        df,
        index_col="immobile",
        pivot_col="tipo",
        value_col="importo",
        mapping={"valore_inesistente": "out"},
    )
    pd.testing.assert_frame_equal(result, df)


def test_pivot_does_not_mutate_input():
    df = _pivot_df()
    original = df.copy(deep=True)
    op_pivot(df, index_col="immobile", pivot_col="tipo", value_col="importo")
    pd.testing.assert_frame_equal(df, original)


# ---------------------------------------------------------------------------
# op_table_diff
# ---------------------------------------------------------------------------


def test_table_diff_detects_added_deleted_modified():
    old = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    new = pd.DataFrame({"id": [1, 2, 4], "val": ["a", "B", "d"]})
    result = op_table_diff(
        [old, new],
        left_keys="id",
        right_keys="id",
    )
    assert "_diff_status" in result.columns
    statuses = set(result["_diff_status"])
    # id=2 modificato (b->B), id=3 eliminato, id=4 aggiunto
    assert "MODIFIED" in statuses
    assert "DELETED" in statuses
    assert "ADDED" in statuses
    # id=1 invariato -> non incluso di default
    assert "UNCHANGED" not in statuses


def test_table_diff_modified_reports_columns_and_old_values():
    old = pd.DataFrame({"id": [1], "val": ["b"]})
    new = pd.DataFrame({"id": [1], "val": ["B"]})
    result = op_table_diff([old, new], left_keys="id", right_keys="id")
    modified = result[result["_diff_status"] == "MODIFIED"].iloc[0]
    assert "val" in str(modified["_diff_columns"])
    assert "b" in str(modified["_diff_old_values"])


def test_table_diff_include_unchanged_yes():
    old = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    new = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    result = op_table_diff(
        [old, new], left_keys="id", right_keys="id", include_unchanged="yes"
    )
    assert set(result["_diff_status"]) == {"UNCHANGED"}
    assert len(result) == 2


def test_table_diff_different_key_names_aligned():
    old = pd.DataFrame({"codice": [1, 2], "val": ["a", "b"]})
    new = pd.DataFrame({"key": [1, 2], "val": ["a", "X"]})
    result = op_table_diff(
        [old, new], left_keys="codice", right_keys="key"
    )
    modified = result[result["_diff_status"] == "MODIFIED"]
    assert len(modified) == 1


def test_table_diff_custom_separator():
    old = pd.DataFrame({"id": [1], "a": ["x"], "b": ["y"]})
    new = pd.DataFrame({"id": [1], "a": ["X"], "b": ["Y"]})
    result = op_table_diff(
        [old, new], left_keys="id", right_keys="id", separator="|"
    )
    modified = result[result["_diff_status"] == "MODIFIED"].iloc[0]
    assert "|" in str(modified["_diff_columns"])


def test_table_diff_compare_columns_subset():
    old = pd.DataFrame({"id": [1], "a": ["x"], "b": ["y"]})
    new = pd.DataFrame({"id": [1], "a": ["x"], "b": ["CHANGED"]})
    # confronta solo 'a' (invariata) -> nessuna MODIFIED su 'b'
    result = op_table_diff(
        [old, new],
        left_keys="id",
        right_keys="id",
        compare_columns="a",
        include_unchanged="yes",
    )
    assert set(result["_diff_status"]) == {"UNCHANGED"}


def test_table_diff_less_than_two_inputs_returns_first():
    df = pd.DataFrame({"id": [1]})
    result = op_table_diff([df], left_keys="id", right_keys="id")
    pd.testing.assert_frame_equal(result, df)


def test_table_diff_empty_inputs_returns_empty_dataframe():
    result = op_table_diff([], left_keys="id", right_keys="id")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_table_diff_no_keys_returns_old_unchanged():
    old = pd.DataFrame({"id": [1], "val": ["a"]})
    new = pd.DataFrame({"id": [1], "val": ["b"]})
    result = op_table_diff([old, new], left_keys="", right_keys="")
    pd.testing.assert_frame_equal(result, old)


def test_table_diff_mismatched_key_count_returns_old():
    old = pd.DataFrame({"a": [1], "b": [2], "v": ["x"]})
    new = pd.DataFrame({"a": [1], "b": [2], "v": ["y"]})
    result = op_table_diff(
        [old, new], left_keys="a,b", right_keys="a"
    )
    pd.testing.assert_frame_equal(result, old)


def test_table_diff_missing_left_key_returns_old():
    old = pd.DataFrame({"id": [1], "v": ["x"]})
    new = pd.DataFrame({"id": [1], "v": ["y"]})
    result = op_table_diff(
        [old, new], left_keys="NON_ESISTE", right_keys="id"
    )
    pd.testing.assert_frame_equal(result, old)


def test_table_diff_missing_right_key_returns_old():
    old = pd.DataFrame({"id": [1], "v": ["x"]})
    new = pd.DataFrame({"id": [1], "v": ["y"]})
    result = op_table_diff(
        [old, new], left_keys="id", right_keys="NON_ESISTE"
    )
    pd.testing.assert_frame_equal(result, old)


def test_table_diff_key_dtype_mismatch_coerced_to_str():
    # chiave int in old, str in new -> stesso valore logico, no falso ADDED/DELETED
    old = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    new = pd.DataFrame({"id": ["1", "2"], "v": ["a", "b"]})
    result = op_table_diff(
        [old, new], left_keys="id", right_keys="id", include_unchanged="yes"
    )
    assert set(result["_diff_status"]) == {"UNCHANGED"}


# ---------------------------------------------------------------------------
# op_melt
# ---------------------------------------------------------------------------


def test_melt_happy_path_wide_to_long():
    df = pd.DataFrame(
        {
            "id": [1, 2],
            "gen": [10, 30],
            "feb": [20, 40],
        }
    )
    result = op_melt(
        df,
        id_columns=["id"],
        value_columns=["gen", "feb"],
        var_name="mese",
        value_name="importo",
    )
    assert list(result.columns) == ["id", "mese", "importo"]
    assert len(result) == 4  # 2 righe x 2 colonne sciolte
    assert set(result["mese"]) == {"gen", "feb"}


def test_melt_value_columns_none_melts_all_others():
    df = pd.DataFrame({"id": [1], "a": [10], "b": [20]})
    result = op_melt(df, id_columns=["id"])
    # default var_name/value_name
    assert "variable" in result.columns
    assert "value" in result.columns
    assert set(result["variable"]) == {"a", "b"}


def test_melt_filters_nonexistent_id_columns():
    df = pd.DataFrame({"id": [1], "a": [10]})
    # id_columns include una colonna inesistente -> filtrata via
    result = op_melt(df, id_columns=["id", "GHOST"], value_columns=["a"])
    assert "GHOST" not in result.columns
    assert "id" in result.columns


def test_melt_invalid_value_columns_fall_back_to_all():
    df = pd.DataFrame({"id": [1], "a": [10], "b": [20]})
    # value_columns tutte inesistenti -> None -> scioglie tutte le non-id
    result = op_melt(
        df, id_columns=["id"], value_columns=["X", "Y"], var_name="v", value_name="val"
    )
    assert set(result["v"]) == {"a", "b"}


def test_melt_value_name_collision_is_renamed():
    # 'value' esiste gia' come colonna sorgente: deve essere auto-rinominato
    df = pd.DataFrame({"id": [1], "value": [99], "x": [5]})
    result = op_melt(df, id_columns=["id", "value"], value_columns=["x"])
    # il default value_name='value' collide -> rinominato con suffisso
    assert "value" in result.columns  # la colonna sorgente resta tra gli id
    assert "value_1" in result.columns  # il nome melt rinominato
    assert result.iloc[0]["value_1"] == 5


def test_melt_var_name_collision_is_renamed():
    df = pd.DataFrame({"id": [1], "variable": ["src"], "x": [5]})
    result = op_melt(df, id_columns=["id", "variable"], value_columns=["x"])
    assert "variable_1" in result.columns


# ---------------------------------------------------------------------------
# op_transpose
# ---------------------------------------------------------------------------


def test_transpose_empty_dataframe_returns_input():
    df = pd.DataFrame({"a": []})
    result = op_transpose(df)
    assert len(result) == 0


def test_transpose_with_id_column_uses_it_as_headers():
    df = pd.DataFrame(
        {
            "metrica": ["fatturato", "costi"],
            "2023": [100, 60],
            "2024": [120, 70],
        }
    )
    result = op_transpose(df, id_column="metrica")
    # metrica diventa l'intestazione delle nuove colonne
    assert "fatturato" in result.columns
    assert "costi" in result.columns
    assert "metrica" in result.columns
    # le ex-colonne (2023, 2024) diventano righe
    assert len(result) == 2


def test_transpose_without_id_column_uses_numeric_col_names():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    result = op_transpose(df)
    # senza id_column le colonne sono col_0, col_1, ...
    assert all(str(c).startswith("col_") for c in result.columns)
    assert len(result) == 2  # 2 colonne originali -> 2 righe


def test_transpose_id_column_not_present_falls_back_to_numeric():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    result = op_transpose(df, id_column="non_esiste")
    assert all(str(c).startswith("col_") for c in result.columns)


def test_transpose_output_columns_renames_remaining():
    df = pd.DataFrame(
        {
            "metrica": ["fatturato", "costi"],
            "2023": [100, 60],
            "2024": [120, 70],
        }
    )
    result = op_transpose(
        df, id_column="metrica", output_columns=["anno_a", "anno_b"]
    )
    cols = list(result.columns)
    # la prima colonna (id) resta 'metrica', le successive rinominate
    assert cols[0] == "metrica"
    assert "anno_a" in cols
    assert "anno_b" in cols


def test_transpose_output_columns_more_than_available_ignored_safely():
    df = pd.DataFrame({"id": ["r1"], "x": [1]})
    # piu' nomi del necessario: non deve crashare
    result = op_transpose(
        df, id_column="id", output_columns=["a", "b", "c", "d", "e"]
    )
    assert isinstance(result, pd.DataFrame)


def test_transpose_roundtrip_shape_swaps_dims():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    result = op_transpose(df)
    # 3 righe x 2 colonne -> trasposto: 2 righe + colonna index reset
    assert result.shape[0] == 2
