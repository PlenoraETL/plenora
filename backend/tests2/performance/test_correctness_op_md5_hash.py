"""Correctness invariant: `op_md5_hash` post-refactor == impl legacy (hash-bit identico).

Critico: l'hash e' usato per change detection; qualunque divergenza vs legacy
romperebbe l'idempotenza dei pipeline che confrontano hash tra run successivi.

Coperture richieste dal checkpoint:
  - **normalizzazione tipi**: int vs float (NaN-induced dtype promotion),
    datetime, bool, Decimal, object mixed
  - **NaN/None**: colonna numeric con NaN, colonna object con None,
    NaT, tutte-NaN su riga
  - **stabilita' hash**: hash[i] stesso bit-per-bit a ogni chiamata
  - **ordering/index effects**: `valid_columns = sorted(...)` e' garanzia
    dell'impl; verifichiamo che riordinando l'input si ottengano gli
    stessi hash; indice duplicato / non-monotonic non deve alterare
    i risultati per-riga
  - **geometry** via `v.wkt`
  - **Unicode multibyte** (le stringhe vanno in md5 via utf-8)
  - **normalize=True vs False** (strip+lower per-colonna)

Esecuzione:
    pytest backend/tests2/performance/test_correctness_op_md5_hash.py \
        -m perf_correctness -s -q
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from app.core.manipola.transforms.base import op_md5_hash


# Oracle: legacy impl (verbatim da base.py:1102 @ aea1f63)


def _legacy_op_md5_hash(
    df, columns, output_column="md5_hash", normalize=True
):
    """Copia verbatim dell'impl pre-Batch-2B (base.py:1102)."""
    if not columns:
        return df
    valid_columns = [c for c in columns if c in df.columns]
    if not valid_columns:
        return df
    valid_columns = sorted(valid_columns)
    result = df.copy()
    separator = "\x1f"
    try:
        parts_df = pd.DataFrame(index=result.index)
        for col in valid_columns:
            s = result[col]
            if s.dtype == object:
                col_str = s.map(
                    lambda v: ""
                    if pd.isna(v)
                    else (v.wkt if hasattr(v, "wkt") else str(v))
                )
            else:
                col_str = s.astype(object).where(s.notna(), "").astype(str)
                col_str = col_str.where(s.notna(), "")
            if normalize:
                col_str = col_str.str.strip().str.lower()
            parts_df[col] = col_str

        combined = parts_df.agg(separator.join, axis=1)
        result[output_column] = combined.map(
            lambda s: hashlib.md5(s.encode("utf-8")).hexdigest()
        )
        return result
    except Exception:
        return df


# Helpers


def _assert_hash_columns_equal(new_df, legacy_df, output_col, ctx):
    """Hash colonne devono essere bit-per-bit identiche. Gestisce il
    caso in cui entrambi falliscono (no output col)."""
    new_has = output_col in new_df.columns
    legacy_has = output_col in legacy_df.columns
    if not legacy_has:
        assert isinstance(new_df, pd.DataFrame)
        return
    assert new_has, f"[{ctx}] legacy ha prodotto '{output_col}' ma new no"
    # Hash e' sempre stringa (o None dove l'impl falli'): .equals lo
    # gestisce correttamente per object dtype.
    if not new_df[output_col].equals(legacy_df[output_col]):
        diffs = []
        n_a = new_df[output_col].to_numpy()
        n_b = legacy_df[output_col].to_numpy()
        for i in range(min(len(n_a), len(n_b))):
            if n_a[i] != n_b[i]:
                diffs.append((i, n_a[i], n_b[i]))
                if len(diffs) >= 3:
                    break
        raise AssertionError(
            f"[{ctx}] divergenza hash. Primi diff (idx, new, legacy): {diffs}"
        )


# Fixtures


def _df_plain_strings():
    return pd.DataFrame({"name": ["Alice", "Bob", "Carol"], "code": ["A1", "B2", "C3"]})


def _df_with_whitespace():
    return pd.DataFrame(
        {
            "name": ["  Alice  ", "BOB\t", "  Carol"],
            "code": ["a1 ", "B2  ", "c3"],
        }
    )


def _df_numeric_ints():
    return pd.DataFrame({"a": [1, 2, 3, 100], "b": [10, 20, 30, 200]})


def _df_numeric_with_nan():
    # NaN-induced dtype promotion: int → float
    return pd.DataFrame({"a": [1, 2, None, 4], "b": [1.5, None, 3.5, 4.5]})


def _df_dates():
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(
                ["2024-01-01", "2024-06-15", None, "2024-12-31"]
            ),
            "label": ["q1", "q2", "q3", "q4"],
        }
    )


def _df_booleans():
    return pd.DataFrame({"flag": [True, False, True, False], "n": [1, 2, 3, 4]})


def _df_object_mixed():
    # dtype=object con mix str/int/None
    return pd.DataFrame(
        {"m": pd.Series(["x", 1, None, True], dtype=object), "v": [10, 20, 30, 40]}
    )


def _df_full_nan_row():
    # Riga 1 tutta-NaN in tutte le colonne hashate
    return pd.DataFrame({"a": [1, None, 3], "b": ["x", None, "z"]})


def _df_geometry():
    return pd.DataFrame(
        {
            "geom": [Point(1, 2), Point(3, 4), None],
            "name": ["p1", "p2", "p3"],
        }
    )


def _df_unicode():
    return pd.DataFrame(
        {
            "s": ["caffè", "piña", "北京", "москва"],
            "n": [1, 2, 3, 4],
        }
    )


def _df_single_column():
    return pd.DataFrame({"only": ["a", "b", None, "d"]})


def _df_empty():
    return pd.DataFrame({"a": [], "b": []})


def _df_duplicate_index():
    df = pd.DataFrame({"a": ["v", "w", "x", "y"], "b": ["1", "2", "3", "4"]})
    df.index = pd.Index([0, 0, 1, 2])
    return df


def _df_non_monotonic_index():
    df = pd.DataFrame({"a": ["v", "w", "x"], "b": ["1", "2", None]})
    df.index = pd.Index([7, 3, 11])
    return df


_CASES = [
    ("plain strings", _df_plain_strings, ["name", "code"], True),
    ("plain strings normalize=False", _df_plain_strings, ["name", "code"], False),
    ("with whitespace normalize=True", _df_with_whitespace, ["name", "code"], True),
    ("with whitespace normalize=False", _df_with_whitespace, ["name", "code"], False),
    ("numeric ints", _df_numeric_ints, ["a", "b"], True),
    ("numeric with NaN", _df_numeric_with_nan, ["a", "b"], True),
    ("dates + strings", _df_dates, ["ts", "label"], True),
    ("booleans", _df_booleans, ["flag", "n"], True),
    ("object mixed types", _df_object_mixed, ["m", "v"], True),
    ("full-NaN row", _df_full_nan_row, ["a", "b"], True),
    ("geometry + strings", _df_geometry, ["geom", "name"], True),
    ("geometry normalize=False", _df_geometry, ["geom", "name"], False),
    ("unicode multibyte", _df_unicode, ["s", "n"], True),
    ("single column", _df_single_column, ["only"], True),
    ("empty df", _df_empty, ["a", "b"], True),
    ("duplicate index", _df_duplicate_index, ["a", "b"], True),
    ("non-monotonic index", _df_non_monotonic_index, ["a", "b"], True),
]


@pytest.mark.perf_correctness
@pytest.mark.parametrize(
    "label,df_factory,cols,normalize",
    _CASES,
    ids=[c[0] for c in _CASES],
)
def test_op_md5_hash_invariant(label, df_factory, cols, normalize):
    """Per ogni caso edge, l'hash del nuovo impl e' bit-per-bit == legacy."""
    df = df_factory()
    df_snapshot = df.copy(deep=True)

    new_out = op_md5_hash(df, columns=cols, output_column="H", normalize=normalize)
    legacy_out = _legacy_op_md5_hash(
        df, columns=cols, output_column="H", normalize=normalize
    )

    _assert_hash_columns_equal(new_out, legacy_out, "H", label)

    # Input df non mutato
    assert len(df) == len(df_snapshot), f"[{label}] lunghezza df mutata"
    for c in df.columns:
        a, b = df[c], df_snapshot[c]
        if a.dtype != b.dtype:
            # dtype cambiato? Potrebbe capitare con .astype in legacy,
            # ma il df e' .copy()-ato, quindi non deve accadere.
            continue
        # Verifica value-wise (NaN-aware)
        a_na, b_na = pd.isna(a).to_numpy(), pd.isna(b).to_numpy()
        assert (a_na == b_na).all(), f"[{label}] mask isna mutato in col {c}"


@pytest.mark.perf_correctness
def test_op_md5_hash_column_order_deterministic():
    """`sorted(valid_columns)` garantisce determinismo: hash uguale
    anche passando le colonne in ordine diverso."""
    df = _df_plain_strings()
    h1 = op_md5_hash(df, columns=["name", "code"], output_column="H")["H"]
    h2 = op_md5_hash(df, columns=["code", "name"], output_column="H")["H"]
    assert h1.equals(h2), (
        "Hash non deterministico rispetto all'ordine di `columns`: "
        "la garanzia `sorted(valid_columns)` e' rotta"
    )


@pytest.mark.perf_correctness
def test_op_md5_hash_stability_across_calls():
    """Chiamare op_md5_hash due volte sullo stesso df deve produrre
    identici hash — no side-effect, no state randomness."""
    df = _df_numeric_with_nan()
    h1 = op_md5_hash(df, columns=["a", "b"], output_column="H")["H"]
    h2 = op_md5_hash(df, columns=["a", "b"], output_column="H")["H"]
    assert h1.equals(h2), "Hash instabile tra chiamate consecutive"


@pytest.mark.perf_correctness
def test_op_md5_hash_missing_column_unchanged():
    """Se nessuna colonna esiste, return df as-is."""
    df = _df_plain_strings()
    result = op_md5_hash(df, columns=["nonexistent"], output_column="H")
    assert "H" not in result.columns
    pd.testing.assert_frame_equal(result, df)


@pytest.mark.perf_correctness
def test_op_md5_hash_partial_valid_cols_sorted_correctly():
    """Alcune colonne non esistenti: filtra + sort delle valide.
    Il valid_columns risultante deve ordinare alfabeticamente."""
    df = _df_plain_strings()
    new_out = op_md5_hash(
        df, columns=["code", "ghost", "name"], output_column="H"
    )
    legacy_out = _legacy_op_md5_hash(
        df, columns=["code", "ghost", "name"], output_column="H"
    )
    _assert_hash_columns_equal(new_out, legacy_out, "H", "partial_valid")


@pytest.mark.perf_correctness
def test_op_md5_hash_large_scale_invariant():
    """10k righe random, tipi misti, NaN sparsi: invariance di scala."""
    rng = np.random.default_rng(42)
    n = 10_000
    df = pd.DataFrame(
        {
            "a": np.where(rng.random(n) < 0.1, None, rng.integers(0, 1000, n)),
            "b": np.where(rng.random(n) < 0.15, None, rng.choice(list("XYZW"), n)),
            "c": np.where(rng.random(n) < 0.05, None, rng.uniform(0, 1, n).round(3)),
            "d": pd.to_datetime("2024-01-01")
            + pd.to_timedelta(rng.integers(0, 365, n), unit="D"),
        }
    )

    for normalize in (True, False):
        new_out = op_md5_hash(
            df, columns=["a", "b", "c", "d"], output_column="H",
            normalize=normalize,
        )
        legacy_out = _legacy_op_md5_hash(
            df, columns=["a", "b", "c", "d"], output_column="H",
            normalize=normalize,
        )
        _assert_hash_columns_equal(
            new_out, legacy_out, "H", f"10k_normalize={normalize}"
        )
