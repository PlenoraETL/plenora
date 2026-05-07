"""
Mixin TRANSACTION_EXECUTION — con strategia transazionale

configurata per DatabaseWriter.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional

import pandas as pd
from sqlalchemy import text

from app.core.carica.models import (
    BatchProgress,
    ColumnMapping,
    TransactionMode,
    WriteResult,
    WriteStatus,
)
from app.core.carica.writer_batch_loop import run_batched_inserts
from app.utils.logger import get_logger

# Stesso logger name di `database_writer.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.carica.writer". Il `StructuredLogger` wrapper delega a
# `_logger.log(...)` sul logger stdlib omonimo (singleton per nome
# quindi handler agganciati nei test B2.0 catturano gli emit dal mixin
logger = get_logger("app.core.carica.writer")


class TransactionExecutionMixin:
    """Mixin per `DatabaseWriter`: batch execution con strategia
    transazionale configurata. Le transaction boundaries
    (`engine.begin` per ALL_OR_NOTHING, `engine.connect` +
    commit/rollback manuali per PER_BATCH) sono incapsulate qui;
    il core executor (`_execute_batch`) e' transaction-agnostic.
    """

    def _execute_in_batches(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        sql_template: str,
        result: WriteResult,
        operation: str,
    ) -> WriteResult:
        """Esegue un'operazione SQL in batch.

        Supporta due strategie transazionali:
        - ALL_OR_NOTHING: unica transazione, rollback totale su errore
        - PER_BATCH: commit ogni batch, i à committati rimangono

        Args:
            df: DataFrame preparato
            columns: mappature colonne
            sql_template: SQL template con placeholder SQLAlchemy
            result: WriteResult da aggiornare
            operation: tipo operazione ("insert", "update", "upsert")

        Returns:
            WriteResult aggiornato.
        """
        batch_size = self.config.batch_size
        total_rows = len(df)
        total_batches = math.ceil(total_rows / batch_size)
        result.batches_total = total_batches

        active_cols = [c for c in columns if not c.skip]
        start_time = time.time()

        logger.info(
            f"Avvio {operation}: {total_rows} righe, "
            f"{total_batches} batch da {batch_size}, "
            f"transazione: {self.config.transaction_mode.value}"
        )

        if self.config.transaction_mode == TransactionMode.ALL_OR_NOTHING:
            result = self._execute_all_or_nothing(
                df, active_cols, sql_template, result, total_batches,
                batch_size, start_time, operation
            )
        else:
            result = self._execute_per_batch(
                df, active_cols, sql_template, result, total_batches,
                batch_size, start_time, operation
            )

        return result

    def _execute_all_or_nothing(
        self, df, columns, sql, result, total_batches,
        batch_size, start_time, operation
    ) -> WriteResult:
        """Esecuzione con unica transazione — commit totale o rollback totale.

        Usa `engine.begin` (idiomatic SQLAlchemy 2.x): context manager
        che committa al normal exit e rollbacka automatic su Exception.
        Coerente col pattern di `_truncate_insert_atomic` /
        `_replace_atomic` — intent di atomicita' esplicito.
        """
        try:
            with self.engine.begin() as conn:
                # Oracle: abilita cursori SDO se ci sono geometrie
                if self.db_type == "oracle":
                    self._oracle_setup(conn)

                def _process_batch(batch_df, batch_num, _conn=conn):
                    rows_in_batch = self._execute_batch(
                        _conn, batch_df, columns, sql, operation
                    )
                    result.rows_written += rows_in_batch
                    result.batches_completed = batch_num + 1

                run_batched_inserts(
                    df=df,
                    result=result,
                    batch_size=batch_size,
                    total_batches=total_batches,
                    start_time=start_time,
                    process_batch=_process_batch,
                    report_progress=self.report_progress,
                )

            # engine.begin ha gia' committato all'exit normale del with
            result.status = WriteStatus.SUCCESS
            logger.info(
                f"{operation} completato: {result.rows_written}/{result.total_rows} "
                f"righe in {time.time() - start_time:.1f}s"
            )

        except Exception as e:
            # engine.begin ha gia' rollbackato automaticamente
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"Errore durante {operation}: {str(e)}"
            result.error_details = repr(e)
            result.rows_written = 0  # rollback
            logger.error(f"[CRASH] Rollback {operation}: {e}", exc_info=True)

        return result

    def _execute_per_batch(
        self, df, columns, sql, result, total_batches,
        batch_size, start_time, operation
    ) -> WriteResult:
        """Esecuzione con commit per batch.

        Prima: apriva una nuova connessione ad ogni batch → pool exhaustion
        con molti batch. Ora riusa UNA connessione e fa commit dopo ogni
        batch. Se un , fa rollback e continua sul successivo
        con una connessione fresca.
        """
        # Mutable holder: la connection viene chiusa+riaperta dopo
        # ogni
        # Lista a 1 elemento per consentire mutation dal closure
        # senza `nonlocal` esplicito
        conn_holder = [self.engine.connect()]
        try:
            if self.db_type == "oracle":
                self._oracle_setup(conn_holder[0])

            def _process_batch(batch_df, batch_num):
                try:
                    rows_in_batch = self._execute_batch(
                        conn_holder[0], batch_df, columns, sql, operation
                    )
                    conn_holder[0].commit()
                    result.rows_written += rows_in_batch
                    result.batches_completed = batch_num + 1
                except Exception as e:
                    try:
                        conn_holder[0].rollback()
                    except Exception as rb_err:
                        logger.debug(
                            "[BATCH] conn.rollback() post errore batch fallito, ignoro: %s: %s",
                            type(rb_err).__name__,
                            rb_err,
                        )
                    # Chiudi e riapri la connessione dopo errore
                    try:
                        conn_holder[0].close()
                    except Exception as cl_err:
                        logger.debug(
                            "[BULK] conn.close() durante retry fallito, ignoro: %s: %s",
                            type(cl_err).__name__,
                            cl_err,
                        )
                    conn_holder[0] = self.engine.connect()
                    if self.db_type == "oracle":
                        self._oracle_setup(conn_holder[0])
                    failed_rows = len(batch_df)
                    result.rows_failed += failed_rows
                    logger.error(
                        f"Errore batch {batch_num + 1}/{total_batches}: {e}"
                    )

            run_batched_inserts(
                df=df,
                result=result,
                batch_size=batch_size,
                total_batches=total_batches,
                start_time=start_time,
                process_batch=_process_batch,
                report_progress=self.report_progress,
            )
        finally:
            try:
                conn_holder[0].close()
            except Exception as cl_err:
                logger.debug(
                    "[BATCH] conn.close() in finally fallito, ignoro: %s: %s",
                    type(cl_err).__name__,
                    cl_err,
                )

        # Stato finale
        if result.rows_failed == 0:
            result.status = WriteStatus.SUCCESS
        elif result.rows_written > 0:
            result.status = WriteStatus.PARTIAL
        else:
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = "Tutti i batch falliti"

        logger.info(
            f"{operation} completato: {result.rows_written} scritte, "
            f"{result.rows_failed} fallite in {time.time() - start_time:.1f}s"
        )
        return result

    def _execute_batch(
        self,
        conn,
        batch_df: pd.DataFrame,
        columns: List[ColumnMapping],
        sql: str,
        operation: str,
        target_table: Optional[str] = None,
        target_schema: Optional[str] = None,
    ) -> int:
        """Esegue l'operazione SQL in batch.

        Strategia post-rimozione bulk native paths (2026-04-23):
        1. **Dispatcher bulk** (`bulk_insert` scaffolding): invocato se
           `is_bulk_enabled` e' True. Post-rimozione COPY Postgres
           (kickoff.md §9.6) + LOAD DATA MySQL (§9.5), il dispatcher
           raise sempre `BulkInsertNotSupportedError(reason=
           "dialect_not_supported")` per ogni dialect. Utile solo come
           source di telemetria `event=bulk.fallback` + correlation IDs
           per osservabilita' post-deploy.
        2. **executemany** (path unico effettivo): `conn.execute(sql,
           params_list)` con lista costruita via `_build_params`.
           SQLAlchemy mappa a driver-native batching. Per MSSQL il boost
           residuo arriva da `fast_executemany=True` sull'engine factory
           (`app/db/query_executor.py:_crea_engine_mssql`).

        Kill-switch `CARICA_BULK_INSERT=off` skippa il tentativo bulk
        scaffolding (funzionalmente equivalente post-rimozione: entrambi
        i valori producono executemany).

        2. **executemany** (default): unico `execute` con lista params.
           SQLAlchemy mappa a driver-native batching. Errori per-riga
           interpretabili (utile su update/upsert con vincoli unique).

        Args:
            target_table: nome reale della tabella di destinazione per
                il bulk path. **OBBLIGATORIO** quando `sql` punta a una
                tabella diversa da `config.table_name` (stage+swap su
                shadow `__stg`, Oracle interim, ecc.). Se omesso, il
                bulk insert va sulla tabella target finale, rompendo
                la semantica di stage+swap (i dati finirebbero sul
                target invece che sulla shadow).
            target_schema: schema di destinazione, default
                `config.schema_name`. Tipicamente non serve specificarlo
                (le shadow table vivono nello stesso schema del target).

        Returns:
            Numero di righe elaborate con successo.
        """
        if len(batch_df) == 0:
            return 0

        # ── Bulk nativo (opt-out via env) ──
        if operation == "insert":
            from app.core.carica.bulk_writers import (
                BulkInsertNotSupportedError,
                bulk_insert,
                is_bulk_enabled,
            )
            if is_bulk_enabled():
                # Risolvi tabella reale: se il caller ha passato
                # target_table esplicito (stage+swap su shadow), usa
                # quello. Altrimenti il SQL e' direttamente sul
                # target finale → config.table_name e' corretto
                effective_table = target_table or self.config.table_name
                effective_schema = (
                    target_schema if target_schema is not None
                    else self.config.schema_name
                )
                try:
                    return bulk_insert(
                        sa_conn=conn,
                        db_type=self.db_type,
                        schema=effective_schema,
                        table=effective_table,
                        columns=columns,
                        df=batch_df,
                    )
                except BulkInsertNotSupportedError as e:
                    # Fallback voluto: il bulk path non e' applicabile a
                    # questa chiamata. Log a debug per non rumoreggiare
                    # in caso di geometry-always (es. catene RFI
                    logger.debug(
                        f"Bulk insert non applicabile ({e}); "
                        f"fallback a executemany"
                    )

        # ── Fallback: executemany ──
        params_list = [
            self._build_params(row, columns, operation)
            for _, row in batch_df.iterrows()
        ]
        conn.execute(text(sql), params_list)
        return len(params_list)
