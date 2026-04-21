"""Benchmark formalizzato: overhead CLIENT-SIDE bulk vs executemany.

Distinto da `test_baseline_bulk_insert.py` (un singolo run, perf_baseline)
per due ragioni:

  1. E' marcato `perf_bench`: ha una **asserzione di speedup**. Se il
     path bulk client-side regredisce sotto 3x vs il fallback, il test
     fallisce — blocca la regressione senza richiedere tooling extra.

  2. Usa warmup + N run ripetute con median (robusto a outlier del
     singolo run), anche senza `pytest-benchmark` installata.

**Cosa misura**:
  - `_bulk_csv_serialization`: pd.to_csv verso StringIO, equivalente al
    frammento di `bulk_copy_postgres` che costruisce il buffer COPY.
  - `_executemany_params_construction`: iterrows + dict-per-row come
    nel fallback di `database_writer._execute_batch` (riga ~2052).

**Cosa NON misura** (vedi test_bench_bulk_vs_executemany_pg_endtoend):
  - round-trip di rete verso Postgres
  - overhead del protocollo COPY server-side
  - commit/WAL

Per l'end-to-end reale servirebbe un PG test. Qui teniamo solo la
parte "pure CPU/memory" del client, che e' comunque la porzione
osservabile senza deps nuove.

Esecuzione:
    pytest backend/tests2/performance/test_bench_bulk_vs_executemany_clientside.py \
        -m perf_bench -s -q
"""

from __future__ import annotations

import csv
import io
import statistics
import time
from dataclasses import dataclass
from typing import Callable, List

import pandas as pd
import pytest

from .conftest import make_pandas_df


# ─────────────────────────────────────────────────────────────────────
# Surrogates dei due path (identici a test_baseline_bulk_insert.py
# per assicurare che baseline e bench misurino la stessa cosa)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeColumnMapping:
    source_column: str
    dest_column: str
    skip: bool = False
    is_geometry: bool = False
    sql_type: str = "TEXT"


def _bulk_csv_serialization(df: pd.DataFrame, columns: List[_FakeColumnMapping]) -> int:
    source_cols = [c.source_column for c in columns if not c.skip]
    subset = df[source_cols]
    buf = io.StringIO()
    subset.to_csv(
        buf,
        index=False,
        header=False,
        na_rep="",
        quoting=csv.QUOTE_MINIMAL,
        encoding="utf-8",
    )
    return len(buf.getvalue())


def _executemany_params_construction(
    df: pd.DataFrame, columns: List[_FakeColumnMapping]
) -> int:
    active = [c for c in columns if not c.skip]
    params_list = []
    for _, row in df.iterrows():
        params = {}
        for c in active:
            v = row[c.source_column]
            params[c.dest_column] = None if pd.isna(v) else v
        params_list.append(params)
    return len(params_list)


def _default_columns() -> List[_FakeColumnMapping]:
    return [
        _FakeColumnMapping("id", "id"),
        _FakeColumnMapping("category", "category"),
        _FakeColumnMapping("amount", "amount"),
        _FakeColumnMapping("qty", "qty"),
        _FakeColumnMapping("active", "active"),
        _FakeColumnMapping("created_at", "created_at"),
        _FakeColumnMapping("label", "label"),
    ]


# ─────────────────────────────────────────────────────────────────────
# Mini-benchmark harness (no dep extra)
# ─────────────────────────────────────────────────────────────────────


def _median_runtime(
    fn: Callable, args: tuple, warmup: int = 1, rounds: int = 5
) -> float:
    """Esegue `fn(*args)` warmup volte (scartate), poi `rounds` volte
    misurate. Restituisce la mediana dei tempi in secondi.

    La mediana e' piu' robusta alla media su N piccoli: esclude l'outlier
    di un GC pause singolo senza richiedere un trimmed-mean."""
    for _ in range(warmup):
        fn(*args)
    times: List[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(*args)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


# ─────────────────────────────────────────────────────────────────────
# Bench con assertion
# ─────────────────────────────────────────────────────────────────────

# Soglia conservativa: la baseline misurata e' ~19x, ma soglia a 3x
# evita failure per micro-regression su hardware diverso. Se la
# metrica dovesse scendere sotto 3x e' un segnale reale (es.
# to_csv cambia costi O su versione nuova pandas, o la df-build
# diventa piu' veloce di quanto previsto).
_MIN_SPEEDUP = 3.0

# Dataset sizes. 10k e 50k coprono dominio tipico; 100k escluso dal
# bench per non blocare la suite (esecuzione ~2.5s per run × 6 run
# × 3 size = ~45s). Il 100k resta misurato nel baseline file.
_BENCH_SIZES = (10_000, 50_000)


@pytest.mark.perf_bench
@pytest.mark.parametrize("n", _BENCH_SIZES)
def test_bench_bulk_csvgen_vs_executemany_params_speedup(n):
    """Il CSV-gen (bulk path) deve essere >= 3x piu' veloce del
    iterrows+dict (fallback path) a dataset 10k/50k.

    Questa asserzione presiede la garanzia funzionale del Deliverable 1:
    se il bulk path regredisse sotto 3x client-side, il lead
    "bulk vs executemany" dichiarato negli invariants §1.1 non
    sarebbe piu' difendibile senza ulteriore evidenza.
    """
    cols = _default_columns()
    df = make_pandas_df(n)

    t_bulk = _median_runtime(_bulk_csv_serialization, (df, cols), warmup=1, rounds=5)
    t_fallback = _median_runtime(
        _executemany_params_construction, (df, cols), warmup=1, rounds=3
    )

    # Evita divisione-per-zero teorica (non dovrebbe mai accadere con
    # perf_counter su misure >0.01s ma paranoia)
    assert t_bulk > 0, f"bulk path misurato {t_bulk}s — misura inattendibile"
    speedup = t_fallback / t_bulk

    print(
        f"\n[bench n={n:>6}] bulk={t_bulk*1000:8.2f}ms  "
        f"fallback={t_fallback*1000:8.2f}ms  "
        f"speedup={speedup:5.2f}x"
    )

    assert speedup >= _MIN_SPEEDUP, (
        f"Bulk CSV-gen speedup regredito: {speedup:.2f}x < {_MIN_SPEEDUP}x "
        f"a n={n} (bulk={t_bulk*1000:.2f}ms vs fallback={t_fallback*1000:.2f}ms). "
        f"Baseline atteso ~19x su pandas 3.0.x."
    )


@pytest.mark.perf_bench
def test_bench_bulk_absolute_throughput_10k():
    """Throughput minimo atteso: bulk CSV-gen deve fare >= 200k rows/s
    su dataset 10k (baseline misurato ~590k rows/s). Floor conservativo.

    Lo scopo e' catturare regressioni che colpiscono bulk senza colpire
    anche il fallback (che farebbe scendere lo speedup ma lascerebbe
    il test sopra `_MIN_SPEEDUP`). Una misura assoluta difende la
    performance end-user.
    """
    cols = _default_columns()
    df = make_pandas_df(10_000)

    t_bulk = _median_runtime(_bulk_csv_serialization, (df, cols), warmup=1, rounds=5)
    rows_per_sec = 10_000 / t_bulk

    print(
        f"\n[bench abs ] bulk 10k  t={t_bulk*1000:.2f}ms  "
        f"throughput={rows_per_sec:,.0f} rows/s"
    )

    _MIN_THROUGHPUT = 200_000
    assert rows_per_sec >= _MIN_THROUGHPUT, (
        f"Bulk throughput regredito: {rows_per_sec:,.0f} rows/s < "
        f"{_MIN_THROUGHPUT:,} rows/s. Baseline atteso ~590k rows/s."
    )
