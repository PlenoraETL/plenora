"""Bench formalizzato: `op_md5_hash` nuovo impl vs legacy vs naive apply.

L'impl pre-Batch-2B di `op_md5_hash` costruisce un parts_df colonna-per-colonna
(ok, vettoriale), poi fa `parts_df.agg(separator.join, axis=1)` seguito da
`combined.map(hashlib.md5(...).hexdigest())`. Le ultime due sono Python
row-iter: due passaggi indipendenti su n righe.

Questo file formalizza il confronto con il legacy e impone speedup ≥ 3x
e throughput minimo. Stessa disciplina di test_bench_op_concat_columns:
warmup + median di N, assertion esplicite, stampa parametri.

Esecuzione:
    pytest backend/tests2/performance/test_bench_op_md5_hash.py \
        -m perf_bench -s -q
"""

from __future__ import annotations

import hashlib
import statistics
import time
from typing import Callable, List

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.base import op_md5_hash

from .conftest import make_pandas_df


# ─────────────────────────────────────────────────────────────────────
# Oracle: legacy impl (verbatim da base.py:1102 @ aea1f63)
# ─────────────────────────────────────────────────────────────────────


def _legacy_op_md5_hash(
    df, columns, output_column="md5_hash", normalize=True
):
    """Copia verbatim dell'impl pre-Batch-2B. Target da battere."""
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


# ─────────────────────────────────────────────────────────────────────
# Naive: apply(axis=1) di un unico row-builder+hash
# ─────────────────────────────────────────────────────────────────────


def _naive_md5_apply(
    df, columns, output_column="md5_hash", normalize=True
):
    """Implementazione apply(axis=1) naive: per riga, costruzione stringa +
    md5. Serve come secondo punto di riferimento."""
    if not columns:
        return df
    valid = sorted(c for c in columns if c in df.columns)
    if not valid:
        return df
    result = df.copy()
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
            s = sep.join(p.strip().lower() for p in parts)
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    result[output_column] = result.apply(_row_hash, axis=1)
    return result


# ─────────────────────────────────────────────────────────────────────
# Bench harness
# ─────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────
# Bench assertions
# ─────────────────────────────────────────────────────────────────────

_MIN_SPEEDUP_VS_LEGACY = 3.0
_MIN_THROUGHPUT_ROWS_PER_SEC = 100_000  # floor conservativo per md5 puro


@pytest.mark.perf_bench
@pytest.mark.parametrize("n", [10_000, 50_000])
def test_bench_md5_hash_speedup_vs_legacy(n):
    """Il nuovo op_md5_hash deve essere >= 3x piu' veloce del legacy su
    10k/50k righe con tipi misti."""
    df = make_pandas_df(n)
    kwargs = dict(
        columns=["id", "category", "amount", "label"],
        output_column="H",
        normalize=True,
    )

    t_new = _median_runtime(op_md5_hash, (df,), kwargs, warmup=1, rounds=3)
    t_legacy = _median_runtime(
        _legacy_op_md5_hash, (df,), kwargs, warmup=1, rounds=2
    )
    t_naive = _median_runtime(
        _naive_md5_apply, (df,), kwargs, warmup=1, rounds=2
    )

    speedup_vs_legacy = t_legacy / t_new
    speedup_vs_naive = t_naive / t_new

    print(
        f"\n[bench md5 n={n:>6}] "
        f"new={t_new*1000:>7.1f}ms  "
        f"legacy={t_legacy*1000:>7.1f}ms  "
        f"naive={t_naive*1000:>7.1f}ms  "
        f"vs_legacy={speedup_vs_legacy:>5.2f}x  "
        f"vs_naive={speedup_vs_naive:>5.2f}x"
    )

    assert speedup_vs_legacy >= _MIN_SPEEDUP_VS_LEGACY, (
        f"op_md5_hash regressione vs legacy: "
        f"{speedup_vs_legacy:.2f}x < {_MIN_SPEEDUP_VS_LEGACY}x a n={n}. "
        f"new={t_new*1000:.1f}ms, legacy={t_legacy*1000:.1f}ms"
    )


@pytest.mark.perf_bench
def test_bench_md5_hash_absolute_throughput_10k():
    """Throughput minimo in rows/s su 10k. Floor a 100k rows/s — md5 e'
    intrinsecamente CPU-bound, ma il nuovo impl non deve essere piu'
    lento di questo ordine di grandezza."""
    df = make_pandas_df(10_000)
    kwargs = dict(
        columns=["id", "category", "amount", "label"],
        output_column="H",
        normalize=True,
    )

    t_new = _median_runtime(op_md5_hash, (df,), kwargs, warmup=1, rounds=5)
    rps = 10_000 / t_new

    print(
        f"\n[bench abs md5] 10k  t={t_new*1000:.2f}ms  "
        f"throughput={rps:,.0f} rows/s"
    )

    assert rps >= _MIN_THROUGHPUT_ROWS_PER_SEC, (
        f"Throughput regredito: {rps:,.0f} rows/s < "
        f"{_MIN_THROUGHPUT_ROWS_PER_SEC:,} rows/s."
    )
