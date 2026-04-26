"""
Implementazione DatabaseWriter per il modulo CARICA.

Architettura
============
Implementa l'interfaccia DestinationWriter per scrivere dati su database
relazionali via SQLAlchemy. Supporta tutti i 5 database di PLENORA-ETL.

Flusso di scrittura (metodo write()):
1. Prepara il DataFrame (conversione geometrie WKT, gestione NULL)
2. Genera SQL (INSERT, UPSERT, UPDATE) tramite DDLGenerator
3. Esegue in batch con strategia transazionale configurata
4. Aggiorna progresso ad ogni batch completato
5. Restituisce WriteResult con esito dettagliato

La connessione SQLAlchemy viene creata riusando DefaultEngineFactory
dal modulo db/query_executor.py, garantendo consistenza con ESTRAI.

Gestione geometrie:
- GeoDataFrame con colonna GeoSeries -> converte in WKT
- Colonna stringa con WKT -> passa direttamente
- Colonna con WKB hex -> converte in WKT
- Il DDLGenerator gestisce la conversione WKT -> tipo nativo DB

Uso:
    from app.core.carica.database_writer import DatabaseWriter

    writer = DatabaseWriter(config, engine, db_type, progress_callback)
    result = writer.write(df)
"""

from app.utils.logger import get_logger, get_request_id, get_user_id
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.carica.models import (
    BatchProgress,
    ColumnMapping,
    ConflictPolicy,
    ConflictReport,
    TransactionMode,
    WriteConfig,
    WriteMode,
    WriteResult,
    WriteStatus,
)
from app.core.carica.writer_base import DestinationWriter
from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.conflict_checker import ConflictChecker
from app.core.carica.writer_data_prep import DataPrepMixin
from app.core.carica.writer_introspection import SchemaIntrospectionMixin
from app.core.carica.writer_transaction_exec import TransactionExecutionMixin
from app.core.carica.writer_replace_strategies import ReplaceStrategiesMixin
from app.core.carica.writer_preflight_lifecycle import WriterPreflightLifecycleMixin
from app.config import settings


logger = get_logger("app.core.carica.writer")
# Telemetry emits (event=stage_swap.dispatched, ecc.) vanno via stdlib
# logger con stesso name: i campi `extra` diventano attributi del LogRecord,
# cosa che StructuredLogger (wrapper) non supporta di default.
_telemetry_logger = logging.getLogger("app.core.carica.writer")


class DatabaseWriter(
    DataPrepMixin,
    SchemaIntrospectionMixin,
    TransactionExecutionMixin,
    ReplaceStrategiesMixin,
    WriterPreflightLifecycleMixin,
    DestinationWriter,
):
    """Scrive dati su database relazionali via SQLAlchemy.

    Attributes:
        engine: SQLAlchemy engine per la connessione
        db_type: tipo database (postgres, oracle, mssql, mysql, mariadb)
        ddl: generatore DDL per il dialetto corrente

    Mixin pattern (Phase 5 B2.x):
      - `DataPrepMixin` (`writer_data_prep.py`, B2.1) fornisce i 4 metodi
        data-prep: `_prepare_dataframe`, `_fix_mixed_type_columns`,
        `_geometry_to_wkt`, `_build_params`.
      - `SchemaIntrospectionMixin` (`writer_introspection.py`, B2.2) fornisce
        i 6 metodi schema introspection: `table_exists`,
        `get_existing_columns`, `get_schemas`, `get_tables`,
        `_table_exists_for`, `_compute_schema_hints`.
      - `TransactionExecutionMixin` (`writer_transaction_exec.py`, B2.3)
        fornisce i 4 metodi batch execution con strategia transazionale:
        `_execute_in_batches` (dispatcher), `_execute_all_or_nothing`
        (single tx via `engine.begin()`), `_execute_per_batch` (commit
        per batch via `engine.connect()`), `_execute_batch` (core
        executor transaction-agnostic). Le transaction boundaries restano
        identiche al pre-extraction.
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
      MRO: DatabaseWriter → DataPrepMixin → SchemaIntrospectionMixin →
      TransactionExecutionMixin → ReplaceStrategiesMixin →
      WriterPreflightLifecycleMixin → DestinationWriter. I metodi
      restano accessibili come `DatabaseWriter.<method>` e
      `inspect.getsource(DatabaseWriter.<method>)` legge il source dal
      mixin tramite `__code__.co_filename` + line numbers.
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


    # Phase 5 B2.5b cutover: cluster PREFLIGHT_LIFECYCLE (`validate`,
    # `_check_create_permission`, `_assert_safe_write_config`,
    # `_build_initial_result`, `check_conflicts`, `create_table`,
    # `drop_table`, `truncate_table`) spostato in
    # `app.core.carica.writer_preflight_lifecycle.WriterPreflightLifecycleMixin`.
    # `DatabaseWriter` eredita dal mixin (vedi class declaration sopra).
    # Le call-site `self.<method>(...)` da `_dispatch_write_mode` /
    # `write` continuano a risolvere via MRO senza modifiche.
    # Warning di `_assert_safe_write_config` catturato dal test
    # modernizzato B2.5a via handler stdlib agganciato al logger
    # canonico `"app.core.carica.writer"` per nome.
    # Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2.

    # Phase 5 B2.2 cutover: `_compute_schema_hints` spostato in
    # `SchemaIntrospectionMixin` (DataFrame-side schema introspection
    # per DDL planning). Vedi class declaration + writer_introspection.py.

    def _dispatch_write_mode(
        self,
        prepared_df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> Tuple[WriteResult, bool]:
        """Esegue il branch di write per `self.config.write_mode`.

        Estratto da `write()` (C4.3 Phase 4). Pure routing: nessuna
        modifica di algoritmo, nessun cambio di transaction boundary,
        nessuna riscrittura dei lower-level helper (`_batch_insert`,
        `_batch_update`, `_batch_upsert`, `_replace_atomic`,
        `_truncate_insert_*`).

        Ritorna `(result, early_return)`:
          - `early_return=True` -> il caller `write()` deve restituire
            `result` cosi' com'e', SENZA assegnare `completed_at` finale.
            Tre branch usano questo flag per preservare il comportamento
            bit-per-bit pre-C4.3:
              * REPLACE atomic: assegna `completed_at` qui dentro e ritorna.
              * APPEND ABORT: NON assegna `completed_at` (preservato).
              * APPEND SKIP con DataFrame vuoto: assegna `completed_at` qui.
          - `early_return=False` -> il caller esegue il finalize standard
            (`completed_at = now()`).

        Eccezioni si propagano fuori dal helper per essere catturate dal
        try/except di `write()` con stesso effetto di pre-C4.3 (set
        `WriteStatus.WRITE_ERROR`, `error_message`, `error_details`,
        log `[CRASH]`).
        """
        mode = self.config.write_mode

        # === CREATE / REPLACE ===
        if mode in (WriteMode.CREATE, WriteMode.REPLACE):
            if mode == WriteMode.REPLACE or (
                mode == WriteMode.CREATE
                and self.config.if_table_exists == "replace"
            ):
                # C6 critical: REPLACE pre-fix faceva drop_table()
                # (DDL auto-commit) + create_table() + _batch_insert().
                # Se l'INSERT falliva, la tabella del cliente non
                # esisteva piu' -> data loss deterministica.
                # Con _replace_atomic: CREATE shadow + INSERT +
                # RENAME atomico -> target mai esposto a stato
                # intermedio. Route REPLACE attraverso questo path
                # e saltiamo l'attuale drop/create/insert in-line.
                # Calcolo dtypes/max_lengths via helper condiviso
                # (C4.2 Phase 4) cosi' sono disponibili a _replace_atomic.
                real_dtypes, max_lengths = self._compute_schema_hints(
                    prepared_df, columns
                )
                result = self._replace_atomic(
                    prepared_df, columns, result, real_dtypes, max_lengths
                )
                # Skip il path non-atomic originale (drop+create+insert)
                result.completed_at = datetime.now(timezone.utc).isoformat()
                return result, True
            # CREATE puro (senza if_table_exists=replace): path
            # originale — no drop (se la tabella esiste e
            # if_table_exists=error, il create_table() fallira').
            # Calcolo dtypes/max_lengths via helper condiviso
            # (C4.2 Phase 4): real_dtypes copre sia nomi colonna del
            # DataFrame sia source/dest dei ColumnMapping (override
            # esplicito); max_lengths abilita VARCHAR2->CLOB su Oracle.
            real_dtypes, max_lengths = self._compute_schema_hints(
                prepared_df, columns
            )
            self.create_table(
                columns, pandas_dtypes=real_dtypes, max_lengths=max_lengths,
            )
            result = self._batch_insert(prepared_df, columns, result)
            return result, False

        # === APPEND ===
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
                        # Blocca l'intera operazione.
                        # Pre-C4.3: completed_at NON viene assegnato qui
                        # ne' dal caller (early-return prima del trailing).
                        # Preserviamo il comportamento bit-per-bit.
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

                    # FORCE: inserisce tutto senza filtri (comportamento default)
                    else:
                        logger.info(
                            f"APPEND FORCE: {conflict_report.total_conflicts} conflitti "
                            f"ignorati, inserimento forzato"
                        )

            result = self._batch_insert(prepared_df, columns, result)
            return result, False

        # === UPDATE ===
        if mode == WriteMode.UPDATE:
            result = self._batch_update(prepared_df, columns, result)
            return result, False

        # === UPSERT ===
        if mode == WriteMode.UPSERT:
            result = self._batch_upsert(prepared_df, columns, result)
            return result, False

        # === TRUNCATE + INSERT ===
        if mode == WriteMode.TRUNCATE_INSERT:
            # C5 fix 1/3: per ALL_OR_NOTHING scegliamo la strategia
            # in base al volume:
            # - sotto CARICA_STAGE_SWAP_THRESHOLD: atomic DELETE+INSERT
            #   in UNA transazione (safe fino a ~500k righe su
            #   qualunque DBMS con UNDO/log ragionevole; no privilegi
            #   DDL richiesti)
            # - sopra soglia: stage+swap (CREATE shadow, INSERT
            #   batch nella shadow, RENAME atomico) — richiede
            #   CREATE TABLE, fallback a atomic con WARNING se i
            #   grant mancano
            if self.config.transaction_mode == TransactionMode.ALL_OR_NOTHING:
                threshold = int(settings.CARICA_STAGE_SWAP_THRESHOLD or 0)
                if threshold > 0 and len(prepared_df) >= threshold:
                    # C6: Oracle usa DBMS_REDEFINITION (atomic +
                    # schema preservato). Postgres/MySQL/MSSQL
                    # usano stage+swap standard (Postgres/MySQL
                    # preservano schema nativamente; MSSQL ha
                    # schema replication via introspection).
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
                # per_batch (vedi WARNING difensivo in write() per la
                # combo rischiosa).
                self.truncate_table()
                result = self._batch_insert(prepared_df, columns, result)
            return result, False

        # Mode non riconosciuto (defensive): pre-C4.3 il flow cadeva fuori
        # dal blocco if/elif e arrivava al trailing `completed_at = now()`
        # senza modifiche al result. Preserviamo identico ritornando
        # early_return=False.
        return result, False

    def write(self, df: pd.DataFrame) -> WriteResult:
        """Esegue la scrittura effettiva dei dati.

        Routing basato su write_mode:
        - CREATE: drop (se replace) + create_table + insert batch
        - APPEND: insert batch (con pre-check conflitti opzionale)
        - REPLACE: drop + create + insert
        - UPDATE: update batch con match columns
        - UPSERT: upsert batch con match columns
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
            # Prepara il DataFrame (conversione geometrie, ecc.)
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

    # ========================================================================
    # BATCH INSERT
    # ========================================================================

    def _batch_insert(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Inserisce righe in batch."""
        sql_template, param_names = self.ddl.generate_insert(
            self.config.schema_name,
            self.config.table_name,
            columns,
        )

        return self._execute_in_batches(df, columns, sql_template, result, "insert")


    # Phase 5 B2.4b cutover: cluster REPLACE_STRATEGIES
    # (`_truncate_insert_atomic`, `_truncate_insert_stage_swap`,
    # `_truncate_insert_oracle_redef`, `_replace_atomic`,
    # `_mssql_replicate_schema_to_shadow`, `_generate_single_rename`)
    # spostato in
    # `app.core.carica.writer_replace_strategies.ReplaceStrategiesMixin`.
    # `DatabaseWriter` eredita dal mixin (vedi class declaration sopra).
    # Le 6 cleanup guards distribuite su 3 metodi sono ancora verificate
    # da `inspect.getsource(DatabaseWriter._method)` (test #5 B2.0
    # modernizzato). I 3 callsite di `_execute_batch(target_table=...)`
    # nei metodi shadow/interim sono ancora verificati dal test
    # modernizzato B2.4a. Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2.

    # ========================================================================
    # BATCH UPDATE
    # ========================================================================

    def _batch_update(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Aggiorna righe in batch."""
        # Filtra colonne da aggiornare
        if self.config.update_columns:
            update_cols = [c for c in columns
                         if c.dest_column in self.config.update_columns
                         and not c.skip]
        else:
            update_cols = [c for c in columns
                         if c.dest_column not in self.config.match_columns
                         and not c.skip and not c.is_primary_key]

        sql_template, param_names = self.ddl.generate_update(
            self.config.schema_name,
            self.config.table_name,
            update_cols,
            self.config.match_columns,
        )

        return self._execute_in_batches(df, columns, sql_template, result, "update")

    # ========================================================================
    # BATCH UPSERT
    # ========================================================================

    def _batch_upsert(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Esegue UPSERT in batch."""
        # B-BE-61: verifica che esista un UNIQUE/PK constraint sulle match_columns
        # Se non esiste, lo crea automaticamente (CREATE UNIQUE INDEX IF NOT EXISTS)
        if self.db_type == 'postgres' and self.config.match_columns:
            with self.engine.begin() as check_conn:
                for mc in self.config.match_columns:
                    mc = mc.strip()
                    table = self.config.table_name.strip()
                    schema = self.config.schema_name.strip()
                    check_sql = text("""
                        SELECT COUNT(*) FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                         AND tc.table_schema = kcu.table_schema
                        WHERE tc.constraint_type IN ('PRIMARY KEY','UNIQUE')
                          AND tc.table_schema = :schema
                          AND tc.table_name = :table
                          AND kcu.column_name = :col
                    """)
                    constraint_count = check_conn.execute(check_sql, {"schema": schema, "table": table, "col": mc}).scalar()
                    if not constraint_count:
                        # Crea indice UNIQUE automaticamente
                        idx_name = f"uq_{table}_{mc}"
                        create_idx = text(
                            f'CREATE UNIQUE INDEX IF NOT EXISTS "{idx_name}" '
                            f'ON "{schema}"."{table}" ("{mc}")'
                        )
                        try:
                            check_conn.execute(create_idx)
                            logger.info(
                                f"Creato indice UNIQUE automatico: {idx_name} su {schema}.{table}({mc})"
                            )
                        except Exception as idx_err:
                            raise ValueError(
                                f"La colonna '{mc}' non ha un indice UNIQUE sulla tabella "
                                f"'{schema}.{table}' e la creazione automatica è fallita: {idx_err}"
                            ) from idx_err
        sql_template, param_names = self.ddl.generate_upsert(
            self.config.schema_name,
            self.config.table_name,
            columns,
            self.config.match_columns,
            self.config.update_columns or None,
        )

        return self._execute_in_batches(df, columns, sql_template, result, "upsert")

    # ========================================================================
    # MOTORE BATCH GENERICO
    # ========================================================================

    # Phase 5 B2.3 cutover: cluster TRANSACTION_EXECUTION
    # (`_execute_in_batches`, `_execute_all_or_nothing`,
    # `_execute_per_batch`, `_execute_batch`) spostato in
    # `app.core.carica.writer_transaction_exec.TransactionExecutionMixin`.
    # `DatabaseWriter` eredita dal mixin (vedi class declaration sopra).
    # Le transaction boundaries (`engine.begin()` / `engine.connect()` +
    # commit/rollback timing) sono incapsulate nel mixin IDENTICHE al
    # pre-extraction. Vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2.

    # Phase 5 B2.1 cutover: cluster DATA_PREP (`_build_params`,
    # `_prepare_dataframe`, `_fix_mixed_type_columns`, `_geometry_to_wkt`)
    # spostato in `app.core.carica.writer_data_prep.DataPrepMixin`.
    # `DatabaseWriter` eredita da `DataPrepMixin` (vedi class declaration
    # sopra). I 4 metodi restano accessibili come `DatabaseWriter._<method>`
    # via MRO; `inspect.getsource(DatabaseWriter._<method>)` legge il
    # source dal mixin (verificato: `inspect` usa `__code__.co_filename`).
    # Vedi anche `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2.

    # ========================================================================
    # HELPER DATABASE-SPECIFICI
    # ========================================================================

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