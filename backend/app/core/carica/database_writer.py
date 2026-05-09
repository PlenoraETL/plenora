"""
Implementazione DatabaseWriter per il modulo CARICA.

Keeps database loading behavior isolated from orchestration callers.
"""

from app.utils.logger import get_logger
import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.carica.models import (
    BatchProgress,
    ColumnMapping,
    ConflictPolicy,
    TransactionMode,
    WriteConfig,
    WriteMode,
    WriteResult,
    WriteStatus,
)
from app.core.carica.writer_base import DestinationWriter
from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.writer_data_prep import DataPrepMixin
from app.core.carica.writer_introspection import SchemaIntrospectionMixin
from app.core.carica.writer_transaction_exec import TransactionExecutionMixin
from app.core.carica.writer_replace_strategies import ReplaceStrategiesMixin
from app.core.carica.writer_preflight_lifecycle import WriterPreflightLifecycleMixin
from app.core.carica.writer_batch_dispatch import BatchDispatchMixin
from app.config import settings


logger = get_logger("app.core.carica.writer")
# Telemetry emits (event=stage_swap.dispatched, ecc.) vanno via stdlib
# logger con stesso name: i campi `extra` diventano attributi del LogRecord
# cosa che StructuredLogger (wrapper) non supporta di default
_telemetry_logger = logging.getLogger("app.core.carica.writer")


class DatabaseWriter(
    DataPrepMixin,
    SchemaIntrospectionMixin,
    TransactionExecutionMixin,
    ReplaceStrategiesMixin,
    WriterPreflightLifecycleMixin,
    BatchDispatchMixin,
    DestinationWriter,
):
    """Scrive dati su database relazionali via SQLAlchemy.

    Attributes:
        engine: SQLAlchemy engine per la connessione
        db_type: tipo database (postgres, oracle, mssql, mysql, mariadb)
        ddl: generatore DDL per il dialetto corrente

    Mixin pattern (B2.x):
      - `DataPrepMixin` (`writer_data_prep.py`, B2.1) fornisce i 4 metodi
        data-prep: `_prepare_dataframe`, `_fix_mixed_type_columns`,
        `_geometry_to_wkt`, `_build_params`.
      - `SchemaIntrospectionMixin` (`writer_introspection.py`, B2.2) fornisce
        i 6 metodi schema introspection: `table_exists`,
        `get_existing_columns`, `get_schemas`, `get_tables`,
        `_table_exists_for`, `_compute_schema_hints`.
      - `TransactionExecutionMixin` (`writer_transaction_exec.py`, B2.3)
        fornisce i 4 metodi con strategia transazionale:
        `_execute_in_batches` (dispatcher), `_execute_all_or_nothing`
        (single tx via `engine.begin`), `_execute_per_batch` (commit
        per `engine.connect`), `_execute_batch` (core
        executor transaction-agnostic). Le transaction boundaries restano
        identiche al previous implementation.
      - `ReplaceStrategiesMixin` (`writer_replace_strategies.py`, B2.4b)
        fornisce i 6 metodi replace/truncate strategies:
        `_truncate_insert_atomic`, `_truncate_insert_stage_swap`,
        `_truncate_insert_oracle_redef`, `_replace_atomic`, +
        2 helper privati intra-cluster (`_mssql_replicate_schema_to_shadow`,
        `_generate_single_rename`). Cleanup guards e callsite
        `_execute_batch(target_table=...)` verificate da test contract
        modernizzati B2.0/B2.4a via `inspect.getsource(method)`.
      - `WriterPreflightLifecycleMixin` (`writer_preflight_lifecycle.py`,
        B2.5b) fornisce gli 8 metodi preflight + DDL lifecycle:
        `validate`, `_check_create_permission`, `_assert_safe_write_config`,
        `_build_initial_result`, `check_conflicts`, `create_table`,
        `drop_table`, `truncate_table`. Pure pre-batch-write
        preparation, zero transaction-orchestration cross-method.
        Warning informativo di `_assert_safe_write_config` catturato
        da test contract modernizzato B2.5a via handler stdlib
        agganciato al logger canonico per nome.
      - `BatchDispatchMixin` (`writer_batch_dispatch.py`, B2.6) fornisce
        i 3 metodi `_batch_insert`, `_batch_update`,
        `_batch_upsert`. Pattern unico "build SQL template per write
        mode + delegate a `_execute_in_batches`". `_batch_upsert` ha
        SAVEPOINT scope-limitato (PG-only auto-create UNIQUE INDEX
        B-BE-61) preservato bit-per-bit. Livello d'astrazione tra
        `_dispatch_write_mode` (resta in `database_writer.py`) e
        `TransactionExecutionMixin._execute_in_batches`.
      MRO: DatabaseWriter → DataPrepMixin → SchemaIntrospectionMixin →
      TransactionExecutionMixin → ReplaceStrategiesMixin →
      WriterPreflightLifecycleMixin → BatchDispatchMixin →
      DestinationWriter. I metodi restano accessibili come
      `DatabaseWriter.<method>` e `inspect.getsource(DatabaseWriter
      .<method>)` legge il source dal mixin tramite
      `__code__.co_filename` + line numbers.
    """

    def __init__(
        self,
        config: WriteConfig,
        engine: Engine,
        db_type: str,
        progress_callback: Optional[Callable[[BatchProgress], None]] = None,
    ):
        super().__init__(config, progress_callback)
        self.engine = engine
        self.db_type = db_type.lower()
        self.ddl = DDLGenerator(self.db_type)


    def _dispatch_write_mode(
        self,
        prepared_df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> Tuple[WriteResult, bool]:
        """Esegue il branch di write per `self.config.write_mode`.

        Estratto da `write` (C4.3 ). Pure routing: nessuna
        modifica di algoritmo, nessun cambio di transaction boundary,
        nessuna riscrittura dei lower-level helper (`_batch_insert`,
        `_batch_update`, `_batch_upsert`, `_replace_atomic`,
        `_truncate_insert_*`).

        Ritorna `(result, early_return)`:
          - `early_return=True` -> il caller `write` deve restituire
            `result` cosi' com'e', SENZA assegnare `completed_at` finale.
            Tre branch usano questo flag per preservare il comportamento
            bit-per-bit pre-C4.3:
              * REPLACE atomic: assegna `completed_at` qui dentro e ritorna.
              * APPEND ABORT: NON assegna `completed_at` (preservato).
              * APPEND SKIP con DataFrame vuoto: assegna `completed_at` qui.
          - `early_return=False` -> il caller esegue il finalize standard
            (`completed_at = now`).

        Eccezioni si propagano fuori dal helper per essere catturate dal
        try/except di `write` con stesso effetto di pre-C4.3 (set
        `WriteStatus.WRITE_ERROR`, `error_message`, `error_details`,
        log `[CRASH]`).
        """
        mode = self.config.write_mode

        if mode in (WriteMode.CREATE, WriteMode.REPLACE):
            if mode == WriteMode.REPLACE or (
                mode == WriteMode.CREATE
                and self.config.if_table_exists == "replace"
            ):
                real_dtypes, max_lengths = self._compute_schema_hints(
                    prepared_df, columns
                )
                result = self._replace_atomic(
                    prepared_df, columns, result, real_dtypes, max_lengths
                )
                # Skip il path non-atomic originale (drop+create+insert
                result.completed_at = datetime.now(timezone.utc).isoformat()
                return result, True
            real_dtypes, max_lengths = self._compute_schema_hints(
                prepared_df, columns
            )
            self.create_table(
                columns, pandas_dtypes=real_dtypes, max_lengths=max_lengths,
            )
            result = self._batch_insert(prepared_df, columns, result)
            return result, False

        if mode == WriteMode.APPEND:
            # Applica conflict_policy se ci sono match_columns configurate
            if self.config.match_columns:
                conflict_report = self.check_conflicts(
                    prepared_df, self.config.match_columns,
                )
                result.conflict_report = conflict_report

                if conflict_report.has_conflicts:
                    policy = self.config.conflict_policy

                    if policy == ConflictPolicy.ABORT:
                        # Blocca l'intera operazione
                        # Pre-C4.3: completed_at NON viene assegnato qui
                        # ne' dal caller (early-return prima del trailing
                        # Preserviamo il comportamento bit-per-bit
                        result.status = WriteStatus.CONFLICT
                        result.error_message = (
                            f"Operazione annullata: {conflict_report.total_conflicts} "
                            f"conflitti trovati (policy: abort)."
                        )
                        return result, True

                    elif policy == ConflictPolicy.SKIP:
                        # Filtra le righe in conflitto, inserisce solo le nuove
                        if conflict_report.details:
                            conflict_indices = {
                                d.row_index for d in conflict_report.details
                            }
                            pre_filter = len(prepared_df)
                            prepared_df = prepared_df.drop(
                                index=list(conflict_indices), errors='ignore',
                            ).reset_index(drop=True)
                            result.rows_skipped = pre_filter - len(prepared_df)
                            logger.info(
                                f"APPEND SKIP: {result.rows_skipped} righe saltate, "
                                f"{len(prepared_df)} righe da inserire"
                            )
                        if prepared_df.empty:
                            result.status = WriteStatus.SUCCESS
                            result.completed_at = datetime.now(
                                timezone.utc,
                            ).isoformat()
                            return result, True

                    # FORCE: inserisce tutto senza filtri (comportamento default
                    else:
                        logger.info(
                            f"APPEND FORCE: {conflict_report.total_conflicts} conflitti "
                            f"ignorati, inserimento forzato"
                        )

            result = self._batch_insert(prepared_df, columns, result)
            return result, False

        if mode == WriteMode.UPDATE:
            result = self._batch_update(prepared_df, columns, result)
            return result, False

        if mode == WriteMode.UPSERT:
            result = self._batch_upsert(prepared_df, columns, result)
            return result, False

        if mode == WriteMode.TRUNCATE_INSERT:
            # C5 fix 1/3: per ALL_OR_NOTHING scegliamo la strategia
            # in base al volume
            # sotto CARICA_STAGE_SWAP_THRESHOLD: atomic DELETE+INSERT
            # in UNA transazione (safe fino a ~500k righe su
            # qualunque DBMS con UNDO/log ragionevole; no privilegi
            # DDL richiesti
            # sopra soglia: stage+swap (CREATE shadow, INSERT
            # shadow, RENAME atomico) — richiede
            # CREATE TABLE, fallback a atomic con WARNING se i
            # grant mancano
            if self.config.transaction_mode == TransactionMode.ALL_OR_NOTHING:
                threshold = int(settings.CARICA_STAGE_SWAP_THRESHOLD or 0)
                if threshold > 0 and len(prepared_df) >= threshold:
                    # C6: Oracle usa DBMS_REDEFINITION (atomic +
                    # schema preservato). Postgres/MySQL/MSSQL
                    # usano stage+swap standard (Postgres/MySQL
                    # preservano schema nativamente; MSSQL ha
                    # schema replication via introspection
                    if self.db_type == "oracle":
                        logger.info(
                            f"[TRUNCATE_INSERT] {len(prepared_df)} >= "
                            f"{threshold}: uso DBMS_REDEFINITION (Oracle)"
                        )
                        result = self._truncate_insert_oracle_redef(
                            prepared_df, columns, result,
                        )
                    else:
                        logger.info(
                            f"[TRUNCATE_INSERT] {len(prepared_df)} >= "
                            f"{threshold}: uso stage+swap"
                        )
                        result = self._truncate_insert_stage_swap(
                            prepared_df, columns, result,
                        )
                else:
                    result = self._truncate_insert_atomic(
                        prepared_df, columns, result,
                    )
            else:
                # PER_BATCH: semantica "fire-and-commit" dichiarata dal
                # caller. Accettiamo lo stesso trade-off del flow APPEND
                # per_batch (vedi WARNING difensivo in write per la
                # combo rischiosa
                self.truncate_table()
                result = self._batch_insert(prepared_df, columns, result)
            return result, False

        # Mode non riconosciuto (defensive): pre-C4.3 il flow cadeva fuori
        # dal blocco if/elif e arrivava al trailing `completed_at = now`
        # senza modifiche al result. Preserviamo identico ritornando
        # early_return=False
        return result, False

    def write(self, df: pd.DataFrame) -> WriteResult:
        """Esegue la scrittura effettiva dei dati.

        Routing basato su write_mode:
        - CREATE: drop (se replace) + create_table + insert batch
        - APPEND: insert batch (con pre-check conflitti opzionale)
        - REPLACE: drop + create + insert
        - UPDATE: update match columns
        - UPSERT: upsert match columns
        - TRUNCATE_INSERT: truncate + insert batch
        """
        started_at = datetime.now(timezone.utc).isoformat()
        total_rows = len(df)

        result = self._build_initial_result(total_rows, started_at)

        if total_rows == 0:
            result.status = WriteStatus.SUCCESS
            result.completed_at = datetime.now(timezone.utc).isoformat()
            logger.info("DataFrame vuoto, nessuna riga da scrivere")
            return result

        self._assert_safe_write_config()

        try:
            # Prepara il DataFrame (conversione geometrie, ecc
            prepared_df = self._prepare_dataframe(df)
            columns = self.config.column_mappings

            result, early_return = self._dispatch_write_mode(
                prepared_df, columns, result,
            )
            if early_return:
                return result

        except Exception as e:
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = str(e)
            result.error_details = repr(e)
            logger.error(f"[CRASH] Errore scrittura: {e}", exc_info=True)

        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result


    # MOTORE

    # B2.3 boundary: cluster TRANSACTION_EXECUTION
    # `_execute_in_batches`, `_execute_all_or_nothing`
    # `_execute_per_batch`, `_execute_batch`) spostato in
    # `app.core.carica.writer_transaction_exec.TransactionExecutionMixin`
    # `DatabaseWriter` eredita dal mixin (vedi class declaration sopra
    # Le transaction boundaries (`engine.begin` / `engine.connect` +
    # commit/rollback timing) sono incapsulate nel mixin IDENTICHE al


    # HELPER DATABASE-SPECIFICI

    def _oracle_setup(self, conn):
        """Configurazione Oracle per gestione SDO_GEOMETRY.

        Come nel query_executor.py di ESTRAI, Oracle richiede
        ALTER SESSION per abilitare i cursori SDO.
        """
        try:
            conn.execute(text(
                "ALTER SESSION SET SDO_GEOM_METADATA_TABLE = 'USER_SDO_GEOM_METADATA'"
            ))
        except Exception as e:
            # Non critico, procedi comunque
            logger.debug(
                "[ORACLE] ALTER SESSION SDO_GEOM_METADATA_TABLE fallito, ignoro: %s: %s",
                type(e).__name__,
                e,
            )
