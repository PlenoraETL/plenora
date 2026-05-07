"""
Helper canonico per il loop batched-insert + progress reporting.

BE-GO-READY-TRANSFORMATION-ENGINE-012-WRITER-BATCH-LOOP-EXTRACT.
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
