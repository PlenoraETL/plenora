"""Baseline timing: path bulk vs path executemany in database_writer.

Il path canonico e' `database_writer._execute_sql_batch` (2016-2057):
  - operation == 'insert' + is_bulk_enabled() + dialect PG/MySQL
      -> bulk_insert() in bulk_writers.py (COPY FROM / LOAD DATA)
  - altrimenti (o su BulkInsertNotSupportedError)
      -> params_list = [self._build_params(row, ...) for _, row in batch_df.iterrows()]
         conn.execute(text(sql), params_list)

**Misuriamo qui la overhead CLIENT-SIDE** di entrambi i path, senza DB
reale. Questo isola:
  - CSV serialization + StringIO buffer (bulk path)
  - iterrows + _build_params dict construction (executemany fallback)

La parte network+DB si misura solo con un PG reale: gated via env
`PERF_PG_TEST_URL`; se assente il test e' skipped con ragione esplicita.

Scope: dataset 10k, 50k, 100k rows (dominio tipico dei batch in carica).

Esecuzione:
    pytest backend/tests2/performance/test_baseline_bulk_insert.py \
        -m perf_baseline -s -q
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from typing import List

import pandas as pd
import pytest

from .conftest import make_pandas_df, print_timing, time_call


# ─────────────────────────────────────────────────────────────────────
# Simulazione dei due path senza DB
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeColumnMapping:
    """Surrogate di ColumnMapping sufficiente per i due path."""

    source_column: str
    dest_column: str
    skip: bool = False
    is_geometry: bool = False
    sql_type: str = "TEXT"


def _bulk_csv_serialization(df: pd.DataFrame, columns: List[_FakeColumnMapping]) -> int:
    """Replica 1:1 la parte CSV-gen di bulk_copy_postgres (righe 150-163).
    Restituisce la lunghezza del buffer generato (bytes equivalenti)."""
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
    """Replica la parte iterrows + dict-per-row del fallback path
    (database_writer:2052-2055). Restituisce numero dict costruiti.

    Semantica semplificata di `_build_params` (database_writer:2059+):
    None/NaN -> None, altrimenti valore raw (no cast tipo-specifico,
    perche' ci interessa l'overhead iterrows+dict, non il cast)."""
    active = [c for c in columns if not c.skip]
    count = 0
    params_list = []
    for _, row in df.iterrows():
        params = {}
        for c in active:
            v = row[c.source_column]
            params[c.dest_column] = None if pd.isna(v) else v
        params_list.append(params)
        count += 1
    return count


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
# Baselines (no DB)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.perf_baseline
def test_baseline_bulk_vs_executemany_clientside_overhead():
    """Overhead client-side: CSV (bulk) vs iterrows+dict (fallback).

    NOTA IMPORTANTE: questo NON include il network round-trip verso PG.
    Lo speedup reale COPY/STDIN vs executemany e' dominato dal
    protocollo lato server: COPY e' streaming, executemany invia N
    statement. Il DB-bound speedup (~10-100x) si misura solo con PG
    reale in Batch 1.
    """
    print("\n## bulk client-side overhead (no DB)")
    print(
        "| impl                                       |    n_rows |   seconds |   rows/s    |"
    )
    print(
        "|--------------------------------------------|----------:|----------:|------------:|"
    )

    cols = _default_columns()

    for n in (10_000, 50_000, 100_000):
        df = make_pandas_df(n)
        tr = time_call(
            "bulk path: CSV-gen (pd.to_csv)",
            n,
            _bulk_csv_serialization,
            df,
            cols,
        )
        print_timing(tr)

    for n in (10_000, 50_000, 100_000):
        df = make_pandas_df(n)
        tr = time_call(
            "fallback:  iterrows + dict-per-row",
            n,
            _executemany_params_construction,
            df,
            cols,
        )
        print_timing(tr)


# ─────────────────────────────────────────────────────────────────────
# End-to-end vs PG reale (skip se env non configurata)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.perf_baseline
def test_baseline_bulk_vs_executemany_real_postgres_skipped_by_default():
    """Placeholder per la misura end-to-end vs PG reale.

    Gate: env `PERF_PG_TEST_URL` (es. postgresql+psycopg2://user:pwd@host/db).
    Se assente -> skip con reason visibile. In Batch 1 attiveremo questo
    path quando avremo:
      - autorizzazione all'uso di un PG test locale, o
      - la dep `testcontainers[postgres]` approvata via STREAM_REPORT.md.
    """
    url = os.getenv("PERF_PG_TEST_URL")
    if not url:
        pytest.skip(
            "PERF_PG_TEST_URL non configurata. "
            "Il benchmark end-to-end bulk vs executemany "
            "richiede un Postgres test. "
            "Vedi STREAM_REPORT.md § Deps."
        )
    # placeholder — verra' implementato in Batch 1
    pytest.skip(
        "Implementazione end-to-end rimandata a Batch 1 "
        "(dopo approvazione dep o setup PG locale)."
    )
