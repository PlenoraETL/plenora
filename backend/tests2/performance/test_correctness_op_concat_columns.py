"""
Correctness invariant: `op_concat_columns` post-refactor == impl legacy.

Scopo: garantire che la vera vettorizzazione (Batch 2) produca output
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.base import op_concat_columns


# Oracle: legacy impl copiato verbatim da base.py:599 @ afe47ce


def _legacy_op_concat_columns(
    df,
    columns,
    output_column: str = "concatenated",
    separator: str = " ",
    skip_null: bool = True,
):
    """Copia verbatim dell'impl pre-Batch-2. Non toccare: serve come
    reference point per la garanzia di output invariato."""
    if not columns:
        return df
    valid_cols = [c for c in columns if c in df.columns]
    if not valid_cols:
        return df
    result = df.copy()
    try:
        if skip_null:
            NAN_SENTINEL = "\x00__NAN__\x00"
            str_df = result[valid_cols].astype(str)
            for col in valid_cols:
                mask = result[col].isna()
                if mask.any():
                    str_df.loc[mask, col] = NAN_SENTINEL
            joined = str_df.agg(
                lambda parts: separator.join(
                    p for p in parts if p != NAN_SENTINEL
                ),
                axis=1,
            )
            result[output_column] = joined.where(joined != "", None)
        else:
            result[output_column] = (
                result[valid_cols]
                .fillna("")
                .astype(str)
                .agg(separator.join, axis=1)
            )
        return result
    except Exception:
        return df


# Helper: equivalenza value-wise con NaN/None trattati come equivalenti


def _series_equivalent(a: pd.Series, b: pd.Series) -> tuple[bool, str]:
    """Confronto element-wise robusto a NaN/None mix.
    Ritorna (ok, reason_se_diverso)."""
    if len(a) != len(b):
        return False, f"len differ: {len(a)} vs {len(b)}"
    a_na = pd.isna(a).to_numpy()
    b_na = pd.isna(b).to_numpy()
    if not (a_na == b_na).all():
        diff_pos = np.where(a_na != b_na)[0][:3]
        return False, f"NaN-mask differs at positions {diff_pos.tolist()}"
    # valori dove entrambi non-null devono matchare
    both_val = ~a_na & ~b_na
    if both_val.any():
        a_vals = a.to_numpy()[both_val]
        b_vals = b.to_numpy()[both_val]
        if not np.array_equal(a_vals, b_vals):
            diff_idx = np.where(a_vals != b_vals)[0][:3]
            return False, (
                f"values differ at positions {diff_idx.tolist()}: "
                f"a={[a_vals[i] for i in diff_idx]}, "
                f"b={[b_vals[i] for i in diff_idx]}"
            )
    return True, ""


def _assert_output_invariant(new_df: pd.DataFrame, legacy_df: pd.DataFrame,
                              output_col: str, context: str) -> None:
    """Asserisce che la colonna output di `new_df` sia equivalente a
    quella di `legacy_df`, con messaggio diagnostico esplicito.

    Gestione bug pre-esistenti pandas 3.0: se il legacy non ha prodotto
    la colonna (eccezione catturata, return df as-is), accettiamo che
    il nuovo impl possa avere lo stesso comportamento oppure migliore
    (es. gestione empty-df funziona dove legacy crasha). L'invariante
    resta garantita per tutti i casi dove legacy produce output."""
    new_has = output_col in new_df.columns
    legacy_has = output_col in legacy_df.columns
    if not legacy_has:
        # NOTE: legacy failure path (exception catturata → df as-is). Il nuovo
        # impl puo' matchare o migliorare. Documentiamo solo che il df
        # resta un DataFrame valido.
        assert isinstance(new_df, pd.DataFrame)
        return
    assert new_has, (
        f"[{context}] regressione: legacy ha prodotto '{output_col}' "
        f"ma il nuovo impl no"
    )
    ok, reason = _series_equivalent(new_df[output_col], legacy_df[output_col])
    assert ok, f"[{context}] divergenza: {reason}"


# Fixture di casi edge


def _df_plain_strings():
    return pd.DataFrame(
        {"a": ["x", "y", "z"], "b": ["1", "2", "3"], "c": ["p", "q", "r"]}
    )


def _df_partial_nans():
    return pd.DataFrame(
        {
            "a": ["x", None, "z", None, "v"],
            "b": ["1", "2", None, None, None],
            "c": ["p", "q", "r", None, "u"],
        }
    )


def _df_full_nan_row():
    # riga 2 e' tutta-NaN → output legacy: None
    return pd.DataFrame(
        {"a": ["x", "y", None], "b": ["1", "2", None], "c": ["p", "q", None]}
    )


def _df_empty_string_edge():
    # Riga [  NaN, "",  NaN ] — edge: join di solo "" -> "" -> None (legacy)
    # Riga [  "",   "", ""   ] — edge: join di tutte "" -> "--" (non None)
    return pd.DataFrame(
        {
            "a": [None, "", "keep_me"],
            "b": ["", "", "keep_me"],
            "c": [None, "", "keep_me"],
        }
    )


def _df_numeric_date_bool():
    return pd.DataFrame(
        {
            "i": [1, 2, 3, None],
            "f": [1.5, 2.5, None, 4.5],
            "b": [True, False, True, False],
            "d": pd.to_datetime(
                ["2024-01-01", "2024-06-15", None, "2024-12-31"]
            ),
        }
    )


def _df_single_column():
    return pd.DataFrame({"only": ["a", None, "c"]})


def _df_empty():
    return pd.DataFrame({"a": [], "b": []})


def _df_duplicate_index():
    # Indice non unico: [0, 0, 1, 2, 2] — stress test per il path
    # stack+groupby(level=0) che rischia di collassare duplicati
    df = pd.DataFrame(
        {"a": ["v", "w", "x", "y", "z"], "b": ["1", "2", "3", "4", "5"]}
    )
    df.index = pd.Index([0, 0, 1, 2, 2])
    return df


def _df_non_monotonic_index():
    # Indice non monotono
    df = pd.DataFrame(
        {"a": ["v", "w", "x"], "b": ["1", "2", None]}
    )
    df.index = pd.Index([7, 3, 11])
    return df


# Test matrix


_CASES = [
    ("plain strings", _df_plain_strings, ["a", "b", "c"], "-", True),
    ("plain strings sep=space", _df_plain_strings, ["a", "b"], " ", True),
    ("plain strings sep=empty", _df_plain_strings, ["a", "b", "c"], "", True),
    ("partial NaNs skip=True", _df_partial_nans, ["a", "b", "c"], "-", True),
    ("partial NaNs skip=False", _df_partial_nans, ["a", "b", "c"], "-", False),
    ("full-NaN row skip=True", _df_full_nan_row, ["a", "b", "c"], "-", True),
    ("full-NaN row skip=False", _df_full_nan_row, ["a", "b", "c"], "-", False),
    ("empty-string edges", _df_empty_string_edge, ["a", "b", "c"], "-", True),
    ("numeric+date+bool", _df_numeric_date_bool, ["i", "f", "b", "d"], "|", True),
    ("numeric+date+bool skip=False", _df_numeric_date_bool,
     ["i", "f", "b", "d"], "|", False),
    ("single column", _df_single_column, ["only"], "-", True),
    ("empty df", _df_empty, ["a", "b"], "-", True),
    ("duplicate index skip=True", _df_duplicate_index, ["a", "b"], "-", True),
    ("duplicate index skip=False", _df_duplicate_index, ["a", "b"], "-", False),
    ("non-monotonic index", _df_non_monotonic_index, ["a", "b"], "-", True),
]


@pytest.mark.perf_correctness
@pytest.mark.parametrize(
    "label,df_factory,cols,sep,skip",
    _CASES,
    ids=[c[0] for c in _CASES],
)
def test_op_concat_columns_invariant(label, df_factory, cols, sep, skip):
    """Per ogni caso edge, l'output del nuovo impl == output legacy."""
    df = df_factory()
    df_snapshot = df.copy(deep=True)  # per verificare non-mutazione

    new_out = op_concat_columns(
        df, columns=cols, output_column="C", separator=sep, skip_null=skip
    )
    legacy_out = _legacy_op_concat_columns(
        df, columns=cols, output_column="C", separator=sep, skip_null=skip
    )

    _assert_output_invariant(new_out, legacy_out, "C", label)

    # input df non mutato
    ok, reason = _series_equivalent(df["a"], df_snapshot["a"]) if "a" in df.columns else (True, "")
    if "a" in df.columns:
        assert ok, f"[{label}] input df mutato (colonna a): {reason}"


@pytest.mark.perf_correctness
def test_op_concat_columns_missing_column_unchanged():
    """Se nessuna delle columns esiste nel df, ritorna df as-is
    (contract legacy)."""
    df = _df_plain_strings()
    result = op_concat_columns(df, columns=["nonexistent"], output_column="C")
    assert "C" not in result.columns
    # Dataframe equivalente all'input
    pd.testing.assert_frame_equal(result, df)


@pytest.mark.perf_correctness
def test_op_concat_columns_empty_columns_list_unchanged():
    """columns=[] → no-op."""
    df = _df_plain_strings()
    result = op_concat_columns(df, columns=[], output_column="C")
    assert "C" not in result.columns
    pd.testing.assert_frame_equal(result, df)


@pytest.mark.perf_correctness
def test_op_concat_columns_partial_valid_cols():
    """Alcune colonne esistenti, altre no: usa solo quelle valide.
    Semantica legacy: filtro via `c in df.columns`."""
    df = _df_plain_strings()
    new_out = op_concat_columns(
        df, columns=["a", "missing", "c"], output_column="C",
        separator="-", skip_null=True,
    )
    legacy_out = _legacy_op_concat_columns(
        df, columns=["a", "missing", "c"], output_column="C",
        separator="-", skip_null=True,
    )
    _assert_output_invariant(new_out, legacy_out, "C", "partial_valid")


@pytest.mark.perf_correctness
def test_op_concat_columns_large_scale_invariant():
    """10k righe random con NaN sparsi: scale test per garantire
    che la vettorizzazione produca lo stesso risultato del legacy."""
    rng = np.random.default_rng(42)
    n = 10_000
    df = pd.DataFrame(
        {
            "a": np.where(rng.random(n) < 0.1, None, rng.integers(0, 100, n)),
            "b": np.where(rng.random(n) < 0.2, None, rng.choice(list("XYZ"), n)),
            "c": np.where(rng.random(n) < 0.05, None, rng.uniform(0, 1, n).round(3)),
        }
    )

    new_out = op_concat_columns(
        df, columns=["a", "b", "c"], output_column="C",
        separator="|", skip_null=True,
    )
    legacy_out = _legacy_op_concat_columns(
        df, columns=["a", "b", "c"], output_column="C",
        separator="|", skip_null=True,
    )

    _assert_output_invariant(new_out, legacy_out, "C", "large_scale_10k")
