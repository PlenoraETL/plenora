"""Helper canonico per il loop batched-insert + progress reporting.

BE-GO-READY-TRANSFORMATION-ENGINE-012-WRITER-BATCH-LOOP-EXTRACT.

Origine
=======
Estratto da `writer_transaction_exec.py` (×2) +
`writer_replace_strategies.py` (×4) come closure di TE11-R1
(BE-GO-READY-TRANSFORMATION-ENGINE-011 §3.2 + §5).

Razionale: 6 call site avevano la stessa shape strutturale —
slicing del DataFrame in batch fixed-size + invocazione
`_execute_batch(...)` + accumulo `result.rows_written` /
`result.batches_completed` + `report_progress(BatchProgress(...))`.
Il delta fra i 6 sites era limitato a:

  - presenza/assenza di `target_table` kwarg sul `_execute_batch`;
  - per-batch error handling (solo `_execute_per_batch`):
    rollback + close + reopen + accumulo `rows_failed`;
  - tutto il resto identico (slicing, progress payload, mutation).

Questa estrazione collassa la parte STRUTTURALE (slicing + progress)
in un helper unico; il body della singola batch resta a carico del
caller via callback `process_batch(batch_df, batch_num)`. Cosi':

  - i 5 site "atomic" (single-tx ALL_OR_NOTHING + 4 strategie
    truncate/replace) passano una callback minimal: `_execute_batch(...)
    + result mutation`;
  - il site `_execute_per_batch` passa una callback piu' ricca con
    try/except per-batch + reopen connection (via mutable
    conn-holder, vedi callsite migrato).

Boundary contract
=================
- **Helper**: pure-loop. Zero I/O proprio (nessun
  `engine.connect()`, nessun `conn.execute(...)` qui dentro).
  Zero settings/env reads. Zero logger emits dal helper.
- **Callback `process_batch(batch_df, batch_num)`**: callable kw-less,
  ritorna `None`. Il caller possiede la connection, la transazione,
  il `_execute_batch`, e la mutation di `result.rows_written` /
  `result.batches_completed` / `result.rows_failed`.
  Le exception del callback **propagano** fino al caller (il helper
  non le cattura): cosi' i context manager `engine.begin()` /
  `engine.connect()` mantengono il loro contratto di rollback come
  pre-extraction.
- **`report_progress`**: callable iniettato (di solito
  `self.report_progress` del writer). Riceve `BatchProgress`
  immutabile per batch.
- **`result`**: passato per leggere `rows_written` / `total_rows`
  e costruire il payload `BatchProgress`. Il helper NON muta
  `result` (la mutation e' nel callback).

Ordine garantito (bit-per-bit con i 6 site originali):
  1. compute `start_idx`, `end_idx`, slice `batch_df`;
  2. `process_batch(batch_df, batch_num)` (callback);
  3. compute `elapsed = time.time() - start_time`;
  4. `report_progress(BatchProgress(...))` con i campi correnti.

Out-of-scope (per task brief)
=============================
- ❌ NO vectorization di `_build_params` (TE11-R7);
- ❌ NO bulk writer revival;
- ❌ NO move di DDLGenerator / ConflictChecker;
- ❌ NO settings / env reads;
- ❌ NO connection / transaction management interno.
"""

from __future__ import annotations

import time
from typing import Callable

import pandas as pd

from app.core.carica.models import BatchProgress, WriteResult


__all__ = ["run_batched_inserts"]


def run_batched_inserts(
    *,
    df: pd.DataFrame,
    result: WriteResult,
    batch_size: int,
    total_batches: int,
    start_time: float,
    process_batch: Callable[[pd.DataFrame, int], None],
    report_progress: Callable[[BatchProgress], None],
) -> None:
    """Iterazione canonica batched-insert + progress reporting.

    Args:
        df: DataFrame preparato (gia' uscito da `_prepare_dataframe`).
            Il helper esegue solo `df.iloc[start_idx:end_idx]`; non
            assume copia, non muta `df`.
        result: `WriteResult` corrente. Letto per costruire il
            `BatchProgress.rows_written` / `total_rows`. **Non mutato**
            dal helper — la mutation e' a carico del callback.
        batch_size: dimensione fissa di ogni batch (numero righe).
        total_batches: numero totale di batch da iterare. Caller
            calcola tipicamente come `math.ceil(total_rows / batch_size)`
            (con `0` se `total_rows == 0`).
        start_time: epoch-time del momento di "avvio operazione" usato
            per `BatchProgress.elapsed_seconds`. Il caller possiede
            il timer (es. `time.time()` raccolto prima di aprire la
            transazione).
        process_batch: callable invocato una volta per batch con
            `(batch_df, batch_num)`. **Owns**:
              - esecuzione del SQL (tipicamente
                `self._execute_batch(conn, batch_df, ...)`);
              - mutation di `result.rows_written` /
                `result.batches_completed` / `result.rows_failed`;
              - eventuale per-batch try/except (es.
                `_execute_per_batch` con rollback + reopen connection).
            Le exception propagano: il helper non le cattura.
        report_progress: callable invocato dopo ogni batch (anche
            in caso di per-batch failure gestito dal callback) con
            `BatchProgress(batch_number, total_batches, rows_written,
            total_rows, elapsed_seconds)`. Tipicamente
            `self.report_progress` del writer.

    Returns:
        None. Tutti gli effetti sono attraverso il callback +
        report_progress.

    Raises:
        Le exception sollevate dal callback `process_batch` propagano
        invariate (no wrapping). Cosi' i context manager dei caller
        (`engine.begin()`, `engine.connect()`) mantengono il proprio
        contratto di rollback automatico identico al pre-extraction.

    Note:
        Se `total_batches == 0` (DataFrame vuoto) il helper e' no-op
        (nessuna iterazione, nessun progress emesso) — coerente col
        pre-extraction (nessuno dei 6 site originali emetteva
        `BatchProgress` per zero righe).
    """
    total_rows = len(df)
    for batch_num in range(total_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, total_rows)
        batch_df = df.iloc[start_idx:end_idx]

        process_batch(batch_df, batch_num)

        elapsed = time.time() - start_time
        report_progress(BatchProgress(
            batch_number=batch_num + 1,
            total_batches=total_batches,
            rows_written=result.rows_written,
            total_rows=result.total_rows,
            elapsed_seconds=elapsed,
        ))
