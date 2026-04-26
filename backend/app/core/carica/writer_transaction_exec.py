"""
Mixin TRANSACTION_EXECUTION — batch execution con strategia transazionale
configurata per DatabaseWriter.

Origine
=======
Estratto da `app.core.carica.database_writer.DatabaseWriter` come parte
di Phase 5 Batch B2.3 (cluster split + mixin pattern, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2 + §6.1 facade pattern
ratificato in B1; mixin pattern ratificato in B2.1+B2.2).

Razionale del raggruppamento: 4 metodi che insieme implementano
"execute SQL in batch con strategia transazionale". Il dispatcher
(`_execute_in_batches`) sceglie tra:
  - ALL_OR_NOTHING (single tx, commit totale o rollback totale via
    `engine.begin()` SQLAlchemy 2.x idiomatic)
  - PER_BATCH (commit per batch via `engine.connect()` + commit/rollback
    manuali, con riapertura connessione dopo ogni errore)

Contenuto:
  - `_execute_in_batches`        — dispatcher pure-routing su
                                   `self.config.transaction_mode`. No
                                   transaction boundaries proprie.
  - `_execute_all_or_nothing`    — `with self.engine.begin() as conn:`
                                   commit auto su normal exit, rollback
                                   auto su Exception. Coerente col pattern
                                   di `_truncate_insert_atomic` /
                                   `_replace_atomic`.
  - `_execute_per_batch`         — `conn = self.engine.connect()` +
                                   commit/rollback per batch + finally
                                   close. Cleanup difensivo (debug-log
                                   su rollback/close fail). Riapre
                                   connessione dopo ogni errore batch.
  - `_execute_batch`             — core executor transaction-agnostic
                                   (riceve `conn` dal caller). Bulk
                                   dispatcher con fallback executemany.

Transaction boundaries
======================
- `_execute_all_or_nothing` apre tx via `engine.begin()` context
  manager: `__exit__` committa se nessuna exception, rollbacka altrimenti.
  Pattern SQLAlchemy 2.x idiomatic — il commit avviene **all'exit del
  with**, non a fine loop.
- `_execute_per_batch` apre `conn = engine.connect()` (no tx auto).
  Per ogni batch: prova `_execute_batch(conn, ...)` + `conn.commit()`;
  su Exception fa `conn.rollback()` + chiude+riapre connessione +
  registra `rows_failed`. `try/finally` close finale.
- `_execute_batch` NON apre/chiude transazioni: riceve `conn` dal caller
  e fa `conn.execute(text(sql), params_list)`. Transaction-agnostic.
- `_execute_in_batches` pure-routing: nessuna tx, nessuna conn.

Spostando questi 4 metodi in mixin, le transaction boundaries
restano IDENTICHE: stesso `engine.begin()` / `engine.connect()` /
context manager pattern, stesso commit/rollback timing.

Cross-mixin / parent dependencies
==================================
- `_execute_batch → self._build_params(...)` → `DataPrepMixin`
  (sibling mixin, B2.1). MRO risolve.
- `_execute_all_or_nothing / _execute_per_batch → self._oracle_setup(conn)` →
  metodo che **resta in `database_writer.py`** (NON estratto in B2.3).
  MRO: name lookup risolve `self._oracle_setup` direttamente nella classe
  principale `DatabaseWriter`.
- `self.report_progress(...)` → `DestinationWriter` (base class).
- `self.config.{batch_size, transaction_mode}`, `self.engine`,
  `self.db_type` → instance attrs inizializzati da `DatabaseWriter.__init__`.

Pattern: Mixin class
====================
`TransactionExecutionMixin` viene ereditato da `DatabaseWriter`:

    class DatabaseWriter(DataPrepMixin, SchemaIntrospectionMixin,
                         TransactionExecutionMixin, DestinationWriter):
        ...

I 4 metodi restano accessibili come `DatabaseWriter._<method>` via MRO.
`inspect.getsource(DatabaseWriter._<method>)` legge il source DA QUESTO
FILE. Le call-site interne — sia dentro lo stesso cluster (es.
`_execute_in_batches → _execute_all_or_nothing`) sia esterne
(es. `_replace_atomic → _execute_batch`) — usano name lookup
`self._<method>(...)` che risolve via MRO senza modifiche.

Coupling test contract
======================
Verificato pre-implementazione:

1. **Patch instance-level via MRO trasparente**:
   - `patch.object(writer, "_execute_batch", side_effect=boom)` in 11
     file di test (7 integration + 4 unit). Rebinda attributo
     dell'istanza, indipendente dalla posizione fisica del metodo. ✓
   - `writer._execute_in_batches = MagicMock(...)` in
     `test_database_writer_upsert_chain_contract.py:150`. Instance
     attr set, MRO trasparente. ✓

2. **AST scan callsite (verificato safe)**:
   - `test_database_writer_bulk_integration_contract.py:335-370`
     (`test_stage_swap_callsite_propagates_stg_table_to_bulk`) parsa
     `ast.parse(database_writer.py)` e cerca callsite di `_execute_batch`
     con kwarg `target_table=`. I 3 callsite cercati sono dentro
     `_replace_atomic`, `_truncate_insert_stage_swap`,
     `_truncate_insert_oracle_redef` — TUTTI restano in `database_writer.py`
     (cluster strategies, NON spostato in B2.3). Il test verifica i
     CALLSITE (chi chiama), NON la definizione del metodo chiamato.
     Spostare `_execute_batch` in mixin → safe. ✓

3. **`test_database_writer_log_consistency_contract.py` (B2.0
   modernized)**: NON tocca questi 4 metodi. Continua verde.

Logger naming contract
======================
Logger creato esplicitamente come `get_logger("app.core.carica.writer")`
(NON `__name__`) per preservare il contratto osservabile pre-extraction.
Il `StructuredLogger` wrapper di `app/utils/logger.py` delega a
`_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
quindi gli emit dal mixin (qui) finiscono sullo stesso logger
underlying di `database_writer.py` — handler agganciati nei test
B2.0 catturano gli emit indistintamente.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`database_writer.py` al momento di B2.3. Nessuna modifica di logica,
comment, log payload, ordine di operazione, transaction context
manager pattern, commit/rollback timing, cleanup difensivo
debug-log, bulk dispatcher fallback, lazy import di
`app.core.carica.bulk_writers`.
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
from app.utils.logger import get_logger

# Stesso logger name di `database_writer.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.carica.writer". Il `StructuredLogger` wrapper delega a
# `_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
# quindi handler agganciati nei test B2.0 catturano gli emit dal mixin.
logger = get_logger("app.core.carica.writer")


class TransactionExecutionMixin:
    """Mixin per `DatabaseWriter`: batch execution con strategia
    transazionale configurata. Le transaction boundaries
    (`engine.begin()` per ALL_OR_NOTHING, `engine.connect()` +
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
        - PER_BATCH: commit ogni batch, i batch già committati rimangono

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

        Usa `engine.begin()` (idiomatic SQLAlchemy 2.x): context manager
        che committa al normal exit e rollbacka automatic su Exception.
        Coerente col pattern di `_truncate_insert_atomic` /
        `_replace_atomic` — intent di atomicita' esplicito.
        """
        try:
            with self.engine.begin() as conn:
                # Oracle: abilita cursori SDO se ci sono geometrie
                if self.db_type == "oracle":
                    self._oracle_setup(conn)

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, len(df))
                    batch_df = df.iloc[start_idx:end_idx]

                    rows_in_batch = self._execute_batch(
                        conn, batch_df, columns, sql, operation
                    )
                    result.rows_written += rows_in_batch
                    result.batches_completed = batch_num + 1

                    # Progresso
                    elapsed = time.time() - start_time
                    self.report_progress(BatchProgress(
                        batch_number=batch_num + 1,
                        total_batches=total_batches,
                        rows_written=result.rows_written,
                        total_rows=result.total_rows,
                        elapsed_seconds=elapsed,
                    ))

            # engine.begin() ha gia' committato all'exit normale del with
            result.status = WriteStatus.SUCCESS
            logger.info(
                f"{operation} completato: {result.rows_written}/{result.total_rows} "
                f"righe in {time.time() - start_time:.1f}s"
            )

        except Exception as e:
            # engine.begin() ha gia' rollbackato automaticamente
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
        con molti batch. Ora riusa UNA connessione e fa commit() dopo ogni
        batch. Se un batch fallisce, fa rollback e continua sul successivo
        con una connessione fresca.
        """
        conn = self.engine.connect()
        try:
            if self.db_type == "oracle":
                self._oracle_setup(conn)

            for batch_num in range(total_batches):
                start_idx = batch_num * batch_size
                end_idx = min(start_idx + batch_size, len(df))
                batch_df = df.iloc[start_idx:end_idx]

                try:
                    rows_in_batch = self._execute_batch(
                        conn, batch_df, columns, sql, operation
                    )
                    conn.commit()
                    result.rows_written += rows_in_batch
                    result.batches_completed = batch_num + 1
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception as rb_err:
                        logger.debug(
                            "[BATCH] conn.rollback() post errore batch fallito, ignoro: %s: %s",
                            type(rb_err).__name__,
                            rb_err,
                        )
                    # Chiudi e riapri la connessione dopo errore
                    try:
                        conn.close()
                    except Exception as cl_err:
                        logger.debug(
                            "[BATCH] conn.close() pre-riapertura fallito, ignoro: %s: %s",
                            type(cl_err).__name__,
                            cl_err,
                        )
                    conn = self.engine.connect()
                    if self.db_type == "oracle":
                        self._oracle_setup(conn)
                    failed_rows = len(batch_df)
                    result.rows_failed += failed_rows
                    logger.error(
                        f"Errore batch {batch_num + 1}/{total_batches}: {e}"
                    )

                # Progresso
                elapsed = time.time() - start_time
                self.report_progress(BatchProgress(
                    batch_number=batch_num + 1,
                    total_batches=total_batches,
                    rows_written=result.rows_written,
                    total_rows=result.total_rows,
                    elapsed_seconds=elapsed,
                ))
        finally:
            try:
                conn.close()
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
           `is_bulk_enabled()` e' True. Post-rimozione COPY Postgres
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

        2. **executemany** (default): unico `execute()` con lista params.
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
                # target finale → config.table_name e' corretto.
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
                    # in caso di geometry-always (es. catene RFI).
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
