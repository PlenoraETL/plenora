"""
Bench formalizzato: `op_concat_columns` nuovo impl vs legacy vs naive apply.

La baseline (test_baseline_pandas_ops.py) aveva mostrato che l'impl
"""

from __future__ import annotations

import hashlib  # noqa: F401  (reserved — md5 bench usera' stesso harness)
import statistics
import time
from typing import Callable, List

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.base import op_concat_columns

from .conftest import make_pandas_df


# Oracle: legacy impl (verbatim da base.py:599 @ afe47ce)


def _legacy_op_concat_columns(
    df, columns, output_column="concatenated", separator=" ", skip_null=True
):
    """Copia verbatim pre-Batch-2. Target da battere."""
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


# Naive: apply(axis=1) (pre-Fase0, dal baseline file)


def _naive_concat_apply(
    df: pd.DataFrame,
    columns: List[str],
    output_column: str = "concatenated",
    separator: str = " ",
    skip_null: bool = True,
) -> pd.DataFrame:
    """apply(axis=1) pre-vectorization, come nel baseline."""
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


# Bench harness (warmup + median of N)


def _median_runtime(
    fn: Callable, args: tuple, kwargs: dict,
    warmup: int = 1, rounds: int = 3,
) -> float:
    for _ in range(warmup):
        fn(*args, **kwargs)
    times: List[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


# Bench assertions

_MIN_SPEEDUP_VS_LEGACY = 3.0
_MIN_THROUGHPUT_ROWS_PER_SEC = 150_000  # naive era ~180k; il nuovo deve almeno tenere il passo


@pytest.mark.perf_bench
@pytest.mark.parametrize("n", [10_000, 50_000])
def test_bench_concat_columns_speedup_vs_legacy(n):
    """Il nuovo op_concat_columns deve essere >= 3x piu' veloce del
    legacy su 10k/50k righe. Il legacy pre-Batch-2 era ~25k rows/s.
    """
    df = make_pandas_df(n)
    kwargs = dict(
        columns=["category", "label", "qty"],
        output_column="concat",
        separator="-",
        skip_null=True,
    )

    t_new = _median_runtime(op_concat_columns, (df,), kwargs, warmup=1, rounds=3)
    t_legacy = _median_runtime(
        _legacy_op_concat_columns, (df,), kwargs, warmup=1, rounds=2
    )
    t_naive = _median_runtime(
        _naive_concat_apply, (df,), kwargs, warmup=1, rounds=2
    )

    speedup_vs_legacy = t_legacy / t_new
    speedup_vs_naive = t_naive / t_new

    print(
        f"\n[bench concat n={n:>6}] "
        f"new={t_new*1000:>7.1f}ms  "
        f"legacy={t_legacy*1000:>7.1f}ms  "
        f"naive={t_naive*1000:>7.1f}ms  "
        f"vs_legacy={speedup_vs_legacy:>5.2f}x  "
        f"vs_naive={speedup_vs_naive:>5.2f}x"
    )

    assert speedup_vs_legacy >= _MIN_SPEEDUP_VS_LEGACY, (
        f"op_concat_columns regressione vs legacy: "
        f"{speedup_vs_legacy:.2f}x < {_MIN_SPEEDUP_VS_LEGACY}x a n={n}. "
        f"new={t_new*1000:.1f}ms, legacy={t_legacy*1000:.1f}ms"
    )


@pytest.mark.perf_bench
def test_bench_concat_columns_absolute_throughput_10k():
    """Throughput minimo in rows/s su 10k. Floor a 150k rows/s — il
    naive apply era gia' ~180k, la vettorizzazione stack+groupby deve
    tenersi almeno nell'ordine di grandezza."""
    df = make_pandas_df(10_000)
    kwargs = dict(
        columns=["category", "label", "qty"],
        output_column="concat",
        separator="-",
        skip_null=True,
    )

    t_new = _median_runtime(op_concat_columns, (df,), kwargs, warmup=1, rounds=5)
    rps = 10_000 / t_new

    print(
        f"\n[bench abs concat] 10k  t={t_new*1000:.2f}ms  "
        f"throughput={rps:,.0f} rows/s"
    )

    assert rps >= _MIN_THROUGHPUT_ROWS_PER_SEC, (
        f"Throughput regredito: {rps:,.0f} rows/s < "
        f"{_MIN_THROUGHPUT_ROWS_PER_SEC:,} rows/s. "
        f"Baseline naive apply ~180k rows/s; il nuovo deve tenersi sopra."
    )
