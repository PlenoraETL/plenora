"""
Behavioral coverage — app/core/manipola/transforms/joins.py.

Esercita il happy-path e i rami significativi delle tre funzioni pubbliche
del cluster JOINS:

  - op_join      (inner/left/right/outer, dedup colonne, coercion dtype,
                  suffissi _L/_R, gestione NaN-key, cardinality guard,
                  pre-validation chiave mancante)
  - op_union     (concat verticale: vuoto, singolo, multiplo, allineamento
                  colonne, ignore_index)
  - op_cross_join(prodotto cartesiano, fail-soft vuoto/singolo, guard OOM)

Tutti i test costruiscono piccoli pandas.DataFrame inline. Non toccano DB,
HTTP o servizi esterni.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.joins import (
    CROSS_JOIN_STAGE,
    op_cross_join,
    op_join,
    op_union,
)
from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# ---------------------------------------------------------------------------
# op_join — happy path e suffissi
# ---------------------------------------------------------------------------


def test_op_join_inner_happy_path_applies_suffixes_and_keeps_key():
    left = pd.DataFrame({"id": [1, 2, 3], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"id": [2, 3, 4], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="id", right_on="id", how="inner")

    # Solo le chiavi comuni 2 e 3.
    assert len(result) == 2
    # La chiave resta senza suffisso, le altre colonne sono suffissate.
    assert "id" in result.columns
    assert "lv_L" in result.columns
    assert "rv_R" in result.columns
    assert sorted(result["id"].tolist()) == [2, 3]


def test_op_join_left_preserves_all_left_rows():
    left = pd.DataFrame({"id": [1, 2, 3], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"id": [2], "rv": ["x"]})

    result = op_join([left, right], left_on="id", right_on="id", how="left")

    assert len(result) == 3
    # Le righe left senza match hanno rv_R = NaN.
    unmatched = result[result["lv_L"].isin(["a", "c"])]
    assert unmatched["rv_R"].isna().all()


def test_op_join_right_preserves_all_right_rows():
    left = pd.DataFrame({"id": [2], "lv": ["a"]})
    right = pd.DataFrame({"id": [1, 2, 3], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="id", right_on="id", how="right")

    assert len(result) == 3
    unmatched = result[result["rv_R"].isin(["x", "z"])]
    assert unmatched["lv_L"].isna().all()


def test_op_join_outer_union_of_keys():
    left = pd.DataFrame({"id": [1, 2], "lv": ["a", "b"]})
    right = pd.DataFrame({"id": [2, 3], "rv": ["y", "z"]})

    result = op_join([left, right], left_on="id", right_on="id", how="outer")

    # Chiavi 1, 2, 3.
    assert len(result) == 3
    assert sorted(result["id"].dropna().tolist()) == [1, 2, 3]


def test_op_join_different_key_names_drops_right_key_column():
    left = pd.DataFrame({"lk": [1, 2, 3], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"rk": [1, 2, 3], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="lk", right_on="rk", how="inner")

    assert len(result) == 3
    # La chiave left rimane, la right (nome diverso) viene rimossa.
    assert "lk" in result.columns
    assert "rk" not in result.columns
    assert "rv_R" in result.columns


# ---------------------------------------------------------------------------
# op_join — input degeneri
# ---------------------------------------------------------------------------


def test_op_join_single_input_returns_it_unchanged():
    only = pd.DataFrame({"id": [1, 2], "v": ["a", "b"]})
    result = op_join([only], left_on="id", right_on="id", how="inner")
    # Ramo len(inputs) < 2: ritorna il primo input as-is.
    assert result is only
    assert list(result.columns) == ["id", "v"]


def test_op_join_empty_input_list_returns_empty_dataframe():
    result = op_join([], left_on="id", right_on="id", how="inner")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# op_join — pre-validation chiave mancante
# ---------------------------------------------------------------------------


def test_op_join_missing_left_key_raises_keyerror_with_raw_columns():
    left = pd.DataFrame({"a": [1], "b": [2]})
    right = pd.DataFrame({"id": [1], "rv": ["x"]})

    with pytest.raises(KeyError) as exc:
        op_join([left, right], left_on="id", right_on="id")

    msg = str(exc.value)
    assert "id" in msg
    # Colonne raw citate, niente suffisso _L (anti-regressione).
    assert "a" in msg
    assert "_L" not in msg
    assert "[internal]" not in msg


def test_op_join_missing_right_key_raises_keyerror():
    left = pd.DataFrame({"id": [1], "lv": ["a"]})
    right = pd.DataFrame({"foo": [1], "bar": [2]})

    with pytest.raises(KeyError) as exc:
        op_join([left, right], left_on="id", right_on="id")

    msg = str(exc.value)
    assert "id" in msg
    assert "_R" not in msg


# ---------------------------------------------------------------------------
# op_join — dedup colonne duplicate
# ---------------------------------------------------------------------------


def test_op_join_dedup_handles_duplicate_non_key_columns():
    # Left con due colonne "dup" omonime: il dedup deve disambiguare.
    left = pd.DataFrame(
        np.array([[1, 10, 20], [2, 11, 21]]),
        columns=["id", "dup", "dup"],
    )
    right = pd.DataFrame({"id": [1, 2], "rv": ["x", "y"]})

    result = op_join([left, right], left_on="id", right_on="id", how="inner")

    assert len(result) == 2
    # Nessuna collisione di nomi nel risultato.
    assert len(set(result.columns)) == len(result.columns)


def test_op_join_dedup_handles_duplicate_key_column():
    # La chiave compare due volte nel left.
    left = pd.DataFrame(
        np.array([[1, 99], [2, 98]]),
        columns=["id", "id"],
    )
    right = pd.DataFrame({"id": [1, 2], "rv": ["x", "y"]})

    result = op_join([left, right], left_on="id", right_on="id", how="inner")

    assert len(result) == 2
    # La chiave reale resta presente; la duplicata e' stata rinominata.
    assert "id" in result.columns


# ---------------------------------------------------------------------------
# op_join — coercion dtype delle chiavi
# ---------------------------------------------------------------------------


def test_op_join_coerces_numeric_vs_string_keys_to_match():
    left = pd.DataFrame({"k": [1, 2, 3], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": ["1", "2", "3"], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")

    # numeric -> stringa pulita "1"/"2"/"3" allinea le chiavi.
    assert len(result) == 3
    assert sorted(result["lv_L"].tolist()) == ["a", "b", "c"]


def test_op_join_coerces_int_vs_float_keys_both_numeric():
    left = pd.DataFrame(
        {"k": pd.array([1, 2, 3], dtype="int32"), "lv": ["a", "b", "c"]}
    )
    right = pd.DataFrame(
        {"k": pd.array([1.0, 2.0, 3.0], dtype="float64"), "rv": ["x", "y", "z"]}
    )

    result = op_join([left, right], left_on="k", right_on="k", how="inner")

    assert len(result) == 3


def test_op_join_float_noninteger_string_coercion_path():
    # dtype mismatch (float vs object) con float non-intero: forza il
    # fallback astype(str) dentro clean_to_str ("1.5" / "2.5").
    left = pd.DataFrame({"k": [1.5, 2.5], "lv": ["a", "b"]})
    right = pd.DataFrame({"k": ["1.5", "2.5"], "rv": ["x", "y"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")

    assert len(result) == 2
    assert sorted(result["lv_L"].tolist()) == ["a", "b"]


def test_op_join_same_dtype_keys_skip_coercion():
    # Stessi dtype object su entrambi i lati: nessuna coercion, match diretto.
    left = pd.DataFrame({"k": ["a", "b", "c"], "lv": [1, 2, 3]})
    right = pd.DataFrame({"k": ["b", "c", "d"], "rv": [10, 20, 30]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")

    assert len(result) == 2
    assert sorted(result["k"].tolist()) == ["b", "c"]


# ---------------------------------------------------------------------------
# op_join — gestione NaN-key
# ---------------------------------------------------------------------------


def test_op_join_inner_excludes_nan_keys():
    left = pd.DataFrame({"k": [1.0, np.nan, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, 2.0, 3.0], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")

    # La riga NaN e' esclusa: nessun match NaN==NaN.
    assert len(result) == 2
    assert sorted(result["lv_L"].tolist()) == ["a", "c"]


def test_op_join_left_preserves_nan_key_row_with_null_right():
    left = pd.DataFrame({"k": [1.0, np.nan, 3.0], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1.0, 2.0, 3.0], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="left")

    assert len(result) == 3
    nan_row = result[result["lv_L"] == "b"]
    assert len(nan_row) == 1
    assert nan_row["rv_R"].isna().all()
    # La chiave NaN e' ripristinata a NaN (non sentinel) nell'output.
    assert nan_row["k"].isna().all()


def test_op_join_outer_nan_both_sides_no_cross_match():
    left = pd.DataFrame({"k": [1.0, np.nan], "lv": ["a", "b"]})
    right = pd.DataFrame({"k": [1.0, np.nan], "rv": ["x", "y"]})

    result = op_join([left, right], left_on="k", right_on="k", how="outer")

    # 1 match (k=1) + 1 left-NaN + 1 right-NaN, mai matchati fra loro.
    assert len(result) == 3
    cross = result[(result["lv_L"] == "b") & (result["rv_R"] == "y")]
    assert len(cross) == 0


def test_op_join_nan_key_restored_with_different_key_names():
    # Nomi chiave diversi + NaN: il restore deve agire su entrambe le key
    # prima del drop della right key.
    left = pd.DataFrame({"lk": [1.0, np.nan], "lv": ["a", "b"]})
    right = pd.DataFrame({"rk": [1.0, np.nan], "rv": ["x", "y"]})

    result = op_join([left, right], left_on="lk", right_on="rk", how="outer")

    assert len(result) == 3
    # right key droppata.
    assert "rk" not in result.columns
    # nessun valore sentinel residuo nella chiave left.
    sentinel_present = result["lk"].astype(str).str.contains("__JOIN_NULL_").any()
    assert not sentinel_present


# ---------------------------------------------------------------------------
# op_join — cardinality guard (many-to-many)
# ---------------------------------------------------------------------------


def test_op_join_cardinality_guard_blocks_explosive_many_to_many():
    # Il cap interno (_MAX_JOIN_OUTPUT_ROWS) e' 50M, hard-coded come
    # variabile locale: non monkeypatchabile. Lo facciamo scattare con la
    # STIMA pre-merge senza mai materializzare 50M righe — il guard solleva
    # ValueError prima del pd.merge. 8000 x 8000 su una sola chiave = 64M
    # righe stimate > 50M, ma in memoria restano solo due frame da 8000.
    n = 8000
    left = pd.DataFrame({"k": [1] * n, "lv": range(n)})
    right = pd.DataFrame({"k": [1] * n, "rv": range(n)})

    with pytest.raises(ValueError) as exc:
        op_join([left, right], left_on="k", right_on="k", how="inner")

    msg = str(exc.value).lower()
    assert "many-to-many" in msg or "cap" in msg


def test_op_join_cardinality_guard_allows_below_cap():
    # Many-to-many legittimo ma ben sotto i 50M: deve passare e produrre
    # il prodotto per chiave comune.
    left = pd.DataFrame({"k": [1, 1, 2], "lv": ["a", "b", "c"]})
    right = pd.DataFrame({"k": [1, 2, 2], "rv": ["x", "y", "z"]})

    result = op_join([left, right], left_on="k", right_on="k", how="inner")
    # k=1: 2x1=2, k=2: 1x2=2 -> 4 righe.
    assert len(result) == 4


# ---------------------------------------------------------------------------
# op_union
# ---------------------------------------------------------------------------


def test_op_union_empty_input_returns_empty():
    result = op_union([])
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_op_union_single_input_returns_it_unchanged():
    only = pd.DataFrame({"a": [1, 2]})
    result = op_union([only])
    assert result is only


def test_op_union_stacks_rows_and_resets_index():
    a = pd.DataFrame({"x": [1, 2]})
    b = pd.DataFrame({"x": [3, 4]})

    result = op_union([a, b])

    assert len(result) == 4
    assert result["x"].tolist() == [1, 2, 3, 4]
    # ignore_index default True -> indice 0..3.
    assert result.index.tolist() == [0, 1, 2, 3]


def test_op_union_preserves_index_when_ignore_index_false():
    a = pd.DataFrame({"x": [1]}, index=[10])
    b = pd.DataFrame({"x": [2]}, index=[20])

    result = op_union([a, b], ignore_index=False)

    assert len(result) == 2
    assert result.index.tolist() == [10, 20]


def test_op_union_aligns_columns_by_name():
    a = pd.DataFrame({"x": [1], "y": [2]})
    b = pd.DataFrame({"y": [3], "z": [4]})

    result = op_union([a, b])

    # Allineamento per nome: union delle colonne, mancanti -> NaN.
    assert len(result) == 2
    assert set(result.columns) == {"x", "y", "z"}
    assert pd.isna(result.loc[0, "z"])
    assert pd.isna(result.loc[1, "x"])


def test_op_union_three_inputs():
    frames = [pd.DataFrame({"v": [i]}) for i in range(3)]
    result = op_union(frames)
    assert result["v"].tolist() == [0, 1, 2]


# ---------------------------------------------------------------------------
# op_cross_join
# ---------------------------------------------------------------------------


def test_op_cross_join_cartesian_product(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 1_000_000)

    left = pd.DataFrame({"a": [1, 2, 3]})
    right = pd.DataFrame({"b": ["x", "y"]})

    result = op_cross_join([left, right])

    assert len(result) == 6
    assert set(result.columns) >= {"a", "b"}


def test_op_cross_join_empty_input_returns_empty():
    result = op_cross_join([])
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_op_cross_join_single_input_returns_first():
    only = pd.DataFrame({"a": [1, 2, 3]})
    result = op_cross_join([only])
    assert result is only
    assert len(result) == 3


def test_op_cross_join_guard_blocks_above_cap(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 10)

    left = pd.DataFrame({"a": [1, 2, 3, 4]})
    right = pd.DataFrame({"b": [1, 2, 3, 4, 5]})

    with pytest.raises(MemoryLimitExceededError) as exc:
        op_cross_join([left, right])

    e = exc.value
    assert e.stage == CROSS_JOIN_STAGE
    assert e.observed_bytes == 20
    assert e.limit_bytes == 10
    assert e.path_basename == "cross_join_output"


def test_op_cross_join_cap_zero_disables_guard(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setitem(_settings.DEFAULTS, "CROSS_JOIN_MAX_OUTPUT_ROWS", 0)

    left = pd.DataFrame({"a": [1, 2]})
    right = pd.DataFrame({"b": [10, 20, 30]})

    result = op_cross_join([left, right])
    assert len(result) == 6
