"""
Baseline timing: operazioni pandas-based gia' vettorializzate in Fase 0.

Misuriamo contemporaneamente:
"""

from __future__ import annotations

import hashlib
from typing import List

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.base import (
    op_concat_columns,
    op_md5_hash,
    op_table_diff,
)

from .conftest import (
    make_diff_inputs,
    make_pandas_df,
    print_timing,
    time_call,
)


# Naive reconstructions (pre-Fase 0 impl, per baseline comparison)


def _naive_concat_columns(
    df: pd.DataFrame,
    columns: List[str],
    output_column: str = "concatenated",
    separator: str = " ",
    skip_null: bool = True,
) -> pd.DataFrame:
    """Reconstruction della versione apply(axis=1) con lambda pre-vectorization.
    Uso: baseline di confronto per provare lo speedup del path attuale."""
    result = df.copy()
    valid = [c for c in columns if c in df.columns]
    if not valid:
        return result

    def _row_join(row):
        parts = []
        for c in valid:
            v = row[c]
            if pd.isna(v):
                if not skip_null:
                    parts.append("")
                continue
            parts.append(str(v))
        return separator.join(parts) if parts else None

    result[output_column] = result.apply(_row_join, axis=1)
    return result


def _naive_md5_hash(
    df: pd.DataFrame,
    columns: List[str],
    output_column: str = "md5_hash",
    normalize: bool = True,
) -> pd.DataFrame:
    """Reconstruction apply(axis=1) + hashlib per riga."""
    result = df.copy()
    valid = sorted([c for c in columns if c in df.columns])
    if not valid:
        return result

    sep = "\x1f"

    def _row_hash(row):
        parts = []
        for c in valid:
            v = row[c]
            if pd.isna(v):
                parts.append("")
            elif hasattr(v, "wkt"):
                parts.append(v.wkt)
            else:
                parts.append(str(v))
        s = sep.join(parts)
        if normalize:
            s = s.strip().lower()
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    result[output_column] = result.apply(_row_hash, axis=1)
    return result


def _naive_table_diff(
    inputs: List[pd.DataFrame],
    left_keys: List[str],
    right_keys: List[str],
) -> pd.DataFrame:
    """Reconstruction iterrows O(n*m) pre-vectorization.
    Usata solo per baseline comparison. Non supporta tutte le feature
    dell'impl attuale; scopo: misurare l'ordine di grandezza."""
    old = inputs[0].copy().reset_index(drop=True)
    new = inputs[1].copy().reset_index(drop=True)

    # index per lookup veloce (mai fatto nel naive vero pre-Fase0, ma
    # senza questo il test tarda veramente troppo — teniamo il naive
    # "simulato" un po' meno cattivo per rendere l'esecuzione finita)
    old_key_to_idx = {
        tuple(old.loc[i, left_keys].values): i for i in old.index
    }
    new_key_to_idx = {
        tuple(new.loc[i, right_keys].values): i for i in new.index
    }

    rows = []
    # ADDED: in new ma non in old
    for k, ni in new_key_to_idx.items():
        if k not in old_key_to_idx:
            row = new.loc[ni].to_dict()
            row["_diff_status"] = "ADDED"
            rows.append(row)

    # DELETED: in old ma non in new
    for k, oi in old_key_to_idx.items():
        if k not in new_key_to_idx:
            row = old.loc[oi].to_dict()
            row["_diff_status"] = "DELETED"
            rows.append(row)

    # MODIFIED: both, ma con colonne cambiate
    common_value_cols = [
        c
        for c in old.columns
        if c not in left_keys and c in new.columns
    ]
    for k, oi in old_key_to_idx.items():
        if k not in new_key_to_idx:
            continue
        ni = new_key_to_idx[k]
        changed = []
        for c in common_value_cols:
            ov, nv = old.at[oi, c], new.at[ni, c]
            ov_na, nv_na = pd.isna(ov), pd.isna(nv)
            if ov_na and nv_na:
                continue
            if ov_na != nv_na:
                changed.append(c)
            elif str(ov) != str(nv):
                changed.append(c)
        if changed:
            row = new.loc[ni].to_dict()
            row["_diff_status"] = "MODIFIED"
            rows.append(row)

    return pd.DataFrame(rows)


# Baselines (mark perf_baseline = skip di default, attiva con -m perf_baseline)


@pytest.mark.perf_baseline
def test_baseline_op_concat_columns_current_vs_naive():
    """op_concat_columns: current (vectorized) vs naive (apply axis=1).
    Dataset: 100k rows current, 10k rows naive (apply troppo lento)."""
    print("\n## op_concat_columns baseline")
    print(
        "| impl                                          |    n_rows |   seconds |   rows/s    |"
    )
    print(
        "|-----------------------------------------------|----------:|----------:|------------:|"
    )

    for n in (10_000, 100_000):
        df = make_pandas_df(n)
        tr = time_call(
            f"current vectorized op_concat_columns",
            n,
            op_concat_columns,
            df,
            columns=["category", "label", "qty"],
            output_column="concat",
            separator="-",
            skip_null=True,
        )
        print_timing(tr)

    # naive solo su 10k — su 100k apply axis=1 e' ~minuti
    for n in (10_000,):
        df = make_pandas_df(n)
        tr = time_call(
            f"naive    apply(axis=1) _naive_concat_columns",
            n,
            _naive_concat_columns,
            df,
            columns=["category", "label", "qty"],
            output_column="concat",
            separator="-",
            skip_null=True,
        )
        print_timing(tr)


@pytest.mark.perf_baseline
def test_baseline_op_md5_hash_current_vs_naive():
    """op_md5_hash: current (vectorized prep) vs naive (apply axis=1)."""
    print("\n## op_md5_hash baseline")
    print(
        "| impl                                          |    n_rows |   seconds |   rows/s    |"
    )
    print(
        "|-----------------------------------------------|----------:|----------:|------------:|"
    )

    for n in (10_000, 100_000):
        df = make_pandas_df(n)
        tr = time_call(
            "current vectorized op_md5_hash",
            n,
            op_md5_hash,
            df,
            columns=["category", "label", "amount"],
            output_column="h",
            normalize=True,
        )
        print_timing(tr)

    for n in (10_000,):
        df = make_pandas_df(n)
        tr = time_call(
            "naive    apply(axis=1) _naive_md5_hash",
            n,
            _naive_md5_hash,
            df,
            columns=["category", "label", "amount"],
            output_column="h",
            normalize=True,
        )
        print_timing(tr)


@pytest.mark.perf_baseline
def test_baseline_op_table_diff_current_vs_naive():
    """op_table_diff: current (merge+np.where) vs naive (iterrows-like)."""
    print("\n## op_table_diff baseline")
    print(
        "| impl                                          |    n_rows |   seconds |   rows/s    |"
    )
    print(
        "|-----------------------------------------------|----------:|----------:|------------:|"
    )

    for n in (10_000, 50_000):
        old, new = make_diff_inputs(n, changed_ratio=0.1)
        # nota: n_rows riportato = n_old (il piu' grande per confronto)
        tr = time_call(
            "current vectorized op_table_diff",
            n,
            op_table_diff,
            [old, new],
            left_keys=["id"],
            right_keys=["id"],
        )
        print_timing(tr)

    for n in (5_000,):
        old, new = make_diff_inputs(n, changed_ratio=0.1)
        tr = time_call(
            "naive    iterrows    _naive_table_diff",
            n,
            _naive_table_diff,
            [old, new],
            left_keys=["id"],
            right_keys=["id"],
        )
        print_timing(tr)
