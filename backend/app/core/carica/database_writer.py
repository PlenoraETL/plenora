"""
Implementazione DatabaseWriter per il modulo CARICA.

Architettura
============
Implementa l'interfaccia DestinationWriter per scrivere dati su database
relazionali via SQLAlchemy. Supporta tutti i 5 database di URBY-ETL.

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

from app.utils.logger import get_logger
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


logger = get_logger("app.core.carica.writer")


class DatabaseWriter(DestinationWriter):
    """Scrive dati su database relazionali via SQLAlchemy.

    Attributes:
        engine: SQLAlchemy engine per la connessione
        db_type: tipo database (postgres, oracle, mssql, mysql, mariadb)
        ddl: generatore DDL per il dialetto corrente
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

    # ========================================================================
    # VALIDAZIONE
    # ========================================================================

    def validate(self) -> Tuple[bool, str]:
        """Valida le pre-condizioni prima della scrittura."""
        # 1. Test connessione
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            return False, f"Connessione al database fallita: {str(e)}"

        # 2. Verifica tabella per modalità che la richiedono
        exists = self.table_exists()

        if self.config.write_mode == WriteMode.CREATE:
            if exists and self.config.if_table_exists == "error":
                return False, (
                    f"La tabella {self.config.schema_name}.{self.config.table_name} "
                    f"esiste già. Usa 'replace' per sovrascriverla."
                )

        if self.config.write_mode in (
            WriteMode.APPEND, WriteMode.UPDATE,
            WriteMode.UPSERT, WriteMode.TRUNCATE_INSERT
        ):
            if not exists:
                return False, (
                    f"La tabella {self.config.schema_name}.{self.config.table_name} "
                    f"non esiste. Usa modalità CREATE per crearla."
                )

        # 3. Verifica colonne match per UPDATE/UPSERT
        if self.config.write_mode in (WriteMode.UPDATE, WriteMode.UPSERT):
            if not self.config.match_columns:
                return False, (
                    f"La modalità {self.config.write_mode.value} richiede "
                    f"almeno una colonna match (chiave primaria)."
                )

        # 4. Verifica permessi (tentativo CREATE TABLE temporanea)
        if self.config.write_mode in (WriteMode.CREATE, WriteMode.REPLACE):
            ok, err = self._check_create_permission()
            if not ok:
                return False, err

        # 5. Verifica che i dest_column del mapping esistano nella tabella destinazione
        #    (solo per modalità che scrivono su tabella esistente)
        if self.config.write_mode in (
            WriteMode.APPEND, WriteMode.UPDATE,
            WriteMode.UPSERT, WriteMode.TRUNCATE_INSERT
        ) and self.config.column_mappings and exists:
            existing_col_names = {c["name"] for c in self.get_existing_columns()}
            if existing_col_names:
                invalid = [
                    cm.dest_column
                    for cm in self.config.column_mappings
                    if not cm.skip and cm.dest_column not in existing_col_names
                ]
                if invalid:
                    return False, (
                        f"Colonne non trovate nella tabella destinazione: {invalid}. "
                        f"Probabilmente il mapping usa un alias invece del nome colonna reale. "
                        f"Usa 'Mapping Colonne' per correggere la destinazione."
                    )

        return True, ""

    def _check_create_permission(self) -> Tuple[bool, str]:
        """Verifica permessi CREATE TABLE."""
        test_table = f"_urby_perm_test_{int(time.time())}"
        try:
            with self.engine.connect() as conn:
                # Prova a creare e droppare una tabella test
                full_name = self.ddl.qualified_table_name(
                    self.config.schema_name, test_table
                )
                conn.execute(text(f"CREATE TABLE {full_name} (test_col INTEGER)"))
                conn.execute(text(
                    self.ddl.generate_drop_table(self.config.schema_name, test_table)
                ))
                conn.commit()
            return True, ""
        except Exception as e:
            error_msg = str(e).lower()
            if "permission" in error_msg or "privilege" in error_msg:
                return False, (
                    f"Permessi insufficienti per CREATE TABLE nello schema "
                    f"'{self.config.schema_name}': {str(e)}"
                )
            # Potrebbe essere un altro errore, logga ma non blocca
            logger.warning(f"Check permessi CREATE: {e}")
            return True, ""

    # ========================================================================
    # SCHEMA INTROSPECTION
    # ========================================================================

    def table_exists(self) -> bool:
        """Verifica se la tabella destinazione esiste."""
        query = self.ddl.query_table_exists(
            self.config.schema_name, self.config.table_name
        )
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))
                count = result.scalar()
                if count is None:
                    return False
                # Type guard: alcuni driver DB ritornano stringa ("1") o
                # bool. Tentativo di conversione protetta da try/except.
                try:
                    return int(count) > 0
                except (ValueError, TypeError):
                    # Fallback: truthy check (es. "true", "yes", non-empty)
                    return bool(count)
        except Exception as e:
            logger.error(f"[CRASH] Errore verifica esistenza tabella: {e}", exc_info=True)
            return False

    def get_existing_columns(self) -> List[Dict[str, str]]:
        """Recupera le colonne della tabella destinazione."""
        query = self.ddl.query_table_columns(
            self.config.schema_name, self.config.table_name
        )
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))
                return [
                    {"name": row[0], "type": str(row[1])}
                    for row in result
                ]
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero colonne: {e}", exc_info=True)
            return []

    def get_schemas(self) -> List[str]:
        """Recupera la lista degli schema disponibili."""
        query = self.ddl.query_list_schemas()
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))
                return [str(row[0]) for row in result]
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero schema: {e}", exc_info=True)
            return []

    def get_tables(self, schema_name: str) -> List[str]:
        """Recupera la lista delle tabelle in uno schema."""
        query = self.ddl.query_list_tables(schema_name)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))
                return [str(row[0]) for row in result]
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero tabelle: {e}", exc_info=True)
            return []

    # ========================================================================
    # GESTIONE TABELLA
    # ========================================================================

    def create_table(self, columns: List[ColumnMapping], pandas_dtypes: Optional[Dict[str, str]] = None) -> bool:
        """Crea la tabella destinazione."""
        # Genera DDL con dtypes reali dal DataFrame
        if pandas_dtypes is None:
            pandas_dtypes = {}
        create_sql = self.ddl.generate_create_table(
            self.config.schema_name,
            self.config.table_name,
            columns,
            pandas_dtypes,
        )

        try:
            with self.engine.connect() as conn:
                conn.execute(text(create_sql))

                # Indice spaziale
                if self.config.create_spatial_index:
                    geom_cols = [c for c in columns if c.is_geometry]
                    for geom_col in geom_cols:
                        index_sqls = self.ddl.generate_spatial_index(
                            self.config.schema_name,
                            self.config.table_name,
                            geom_col.dest_column,
                            geom_col.srid or self.config.geometry_srid,
                        )
                        for idx_sql in index_sqls:
                            logger.info(f"SPATIAL INDEX: {idx_sql[:200]}...")
                            conn.execute(text(idx_sql))

                # Indice univoco (per supporto UPSERT futuro)
                # B-BE-61: se configurato, crea UNIQUE constraint sulla colonna chiave
                unique_col = getattr(self.config, 'unique_key_column', None)
                if unique_col and self.db_type == 'postgres':
                    unique_col = unique_col.strip()
                    table = self.config.table_name.strip()
                    schema = self.config.schema_name.strip()
                    constraint_name = f"uq_{table}_{unique_col}"
                    unique_sql = (
                        f'ALTER TABLE "{schema}"."{table}" '
                        f'ADD CONSTRAINT "{constraint_name}" UNIQUE ("{unique_col}")'
                    )
                    logger.info(f"UNIQUE CONSTRAINT: {unique_sql}")
                    conn.execute(text(unique_sql))

                conn.commit()
            logger.info(
                f"Tabella {self.config.schema_name}.{self.config.table_name} "
                f"creata con successo"
            )
            return True

        except Exception as e:
            logger.error(f"[CRASH] Errore creazione tabella: {e}", exc_info=True)
            raise

    def drop_table(self) -> bool:
        """Elimina la tabella destinazione."""
        drop_sql = self.ddl.generate_drop_table(
            self.config.schema_name, self.config.table_name
        )
        try:
            with self.engine.connect() as conn:
                conn.execute(text(drop_sql))
                conn.commit()
            logger.info(f"Tabella {self.config.table_name} eliminata")
            return True
        except Exception as e:
            logger.error(f"[CRASH] Errore eliminazione tabella: {e}", exc_info=True)
            return False

    def truncate_table(self) -> bool:
        """Svuota la tabella destinazione."""
        trunc_sql = self.ddl.generate_truncate(
            self.config.schema_name, self.config.table_name
        )
        try:
            with self.engine.connect() as conn:
                conn.execute(text(trunc_sql))
                conn.commit()
            logger.info(f"Tabella {self.config.table_name} svuotata")
            return True
        except Exception as e:
            logger.error(f"[CRASH] Errore truncate tabella: {e}", exc_info=True)
            return False

    # ========================================================================
    # CHECK CONFLITTI
    # ========================================================================

    def check_conflicts(
        self,
        df: pd.DataFrame,
        match_columns: List[str],
    ) -> ConflictReport:
        """Pre-check conflitti per APPEND."""
        checker = ConflictChecker(self.engine, self.db_type)
        return checker.check(
            df,
            self.config.schema_name,
            self.config.table_name,
            match_columns,
        )

    # ========================================================================
    # SCRITTURA PRINCIPALE
    # ========================================================================

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

        result = WriteResult(
            started_at=started_at,
            total_rows=total_rows,
            table_name=f"{self.config.schema_name}.{self.config.table_name}",
            write_mode=self.config.write_mode.value,
        )

        if total_rows == 0:
            result.status = WriteStatus.SUCCESS
            result.completed_at = datetime.now(timezone.utc).isoformat()
            logger.info("DataFrame vuoto, nessuna riga da scrivere")
            return result

        try:
            # Prepara il DataFrame (conversione geometrie, ecc.)
            prepared_df = self._prepare_dataframe(df)
            columns = self.config.column_mappings

            mode = self.config.write_mode

            # === CREATE / REPLACE ===
            if mode in (WriteMode.CREATE, WriteMode.REPLACE):
                if mode == WriteMode.REPLACE or (
                    mode == WriteMode.CREATE
                    and self.config.if_table_exists == "replace"
                ):
                    self.drop_table()
                # Passa i dtypes reali del DataFrame per type mapping corretto
                # Chiavi: sia nome colonna DataFrame che source_column del mapping
                real_dtypes = {col: str(prepared_df[col].dtype) for col in prepared_df.columns}
                for cm in columns:
                    if cm.source_column in prepared_df.columns:
                        real_dtypes[cm.source_column] = str(prepared_df[cm.source_column].dtype)
                    if cm.dest_column in prepared_df.columns:
                        real_dtypes[cm.dest_column] = str(prepared_df[cm.dest_column].dtype)
                self.create_table(columns, pandas_dtypes=real_dtypes)
                result = self._batch_insert(prepared_df, columns, result)

            # === APPEND ===
            elif mode == WriteMode.APPEND:
                # Applica conflict_policy se ci sono match_columns configurate
                if self.config.match_columns:
                    conflict_report = self.check_conflicts(prepared_df, self.config.match_columns)
                    result.conflict_report = conflict_report

                    if conflict_report.has_conflicts:
                        policy = self.config.conflict_policy

                        if policy == ConflictPolicy.ABORT:
                            # Blocca l'intera operazione
                            result.status = WriteStatus.CONFLICT
                            result.error_message = (
                                f"Operazione annullata: {conflict_report.total_conflicts} "
                                f"conflitti trovati (policy: abort)."
                            )
                            return result

                        elif policy == ConflictPolicy.SKIP:
                            # Filtra le righe in conflitto, inserisce solo le nuove
                            if conflict_report.details:
                                conflict_indices = {d.row_index for d in conflict_report.details}
                                pre_filter = len(prepared_df)
                                prepared_df = prepared_df.drop(index=list(conflict_indices), errors='ignore').reset_index(drop=True)
                                result.rows_skipped = pre_filter - len(prepared_df)
                                logger.info(
                                    f"APPEND SKIP: {result.rows_skipped} righe saltate, "
                                    f"{len(prepared_df)} righe da inserire"
                                )
                            if prepared_df.empty:
                                result.status = WriteStatus.SUCCESS
                                result.completed_at = datetime.now(timezone.utc).isoformat()
                                return result

                        # FORCE: inserisce tutto senza filtri (comportamento default)
                        else:
                            logger.info(
                                f"APPEND FORCE: {conflict_report.total_conflicts} conflitti "
                                f"ignorati, inserimento forzato"
                            )

                result = self._batch_insert(prepared_df, columns, result)

            # === UPDATE ===
            elif mode == WriteMode.UPDATE:
                result = self._batch_update(prepared_df, columns, result)

            # === UPSERT ===
            elif mode == WriteMode.UPSERT:
                result = self._batch_upsert(prepared_df, columns, result)

            # === TRUNCATE + INSERT ===
            elif mode == WriteMode.TRUNCATE_INSERT:
                self.truncate_table()
                result = self._batch_insert(prepared_df, columns, result)

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
                            )
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
        """Esecuzione con unica transazione."""
        try:
            with self.engine.connect() as conn:
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

                conn.commit()
                result.status = WriteStatus.SUCCESS
                logger.info(
                    f"{operation} completato: {result.rows_written}/{result.total_rows} "
                    f"righe in {time.time() - start_time:.1f}s"
                )

        except Exception as e:
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
        """Esecuzione con commit per batch."""
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(df))
            batch_df = df.iloc[start_idx:end_idx]

            try:
                with self.engine.connect() as conn:
                    if self.db_type == "oracle":
                        self._oracle_setup(conn)

                    rows_in_batch = self._execute_batch(
                        conn, batch_df, columns, sql, operation
                    )
                    conn.commit()

                result.rows_written += rows_in_batch
                result.batches_completed = batch_num + 1

            except Exception as e:
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
    ) -> int:
        """Esegue una singola operazione SQL per ogni riga del batch.

        Returns:
            Numero di righe elaborate con successo.
        """
        rows_done = 0

        for _, row in batch_df.iterrows():
            params = self._build_params(row, columns, operation)
            conn.execute(text(sql), params)
            rows_done += 1

        return rows_done

    def _build_params(
        self,
        row: pd.Series,
        columns: List[ColumnMapping],
        operation: str,
    ) -> Dict[str, Any]:
        """Costruisce i parametri per una singola riga.

        Converte i valori dal DataFrame nel formato atteso da SQLAlchemy:
        - None/NaN -> None (SQL NULL)
        - Geometria GeoSeries -> WKT string
        - numpy scalar -> Python native
        - datetime -> string ISO
        """
        params = {}

        for col in columns:
            value = row.get(col.source_column)
            param_name = col.dest_column

            # Converti NaN/NaT in None
            if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
                params[param_name] = None
                continue

            # Geometria -> WKT
            if col.is_geometry:
                params[param_name] = self._geometry_to_wkt(value)
                continue

            # numpy scalar -> Python native
            if hasattr(value, 'item'):
                value = value.item()

            # Timestamp/datetime -> ISO string (per compatibilità cross-DB)
            if hasattr(value, 'isoformat'):
                value = value.isoformat()

            params[param_name] = value

        # Per UPDATE: aggiungi parametri match con prefisso "match_"
        if operation == "update":
            for match_col in self.config.match_columns:
                value = row.get(match_col)
                if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
                    params[f"match_{match_col}"] = None
                elif hasattr(value, 'item'):
                    params[f"match_{match_col}"] = value.item()
                else:
                    params[f"match_{match_col}"] = value

        return params

    # ========================================================================
    # PREPARAZIONE DATAFRAME
    # ========================================================================

    def _prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepara il DataFrame per la scrittura.

        - Rileva colonne con tipi misti (es. numeri + stringhe) e le converte a TEXT
        - Converte GeoSeries in colonne WKT string
        - Rinomina colonne secondo il mapping
        - Rimuove colonne skip
        """
        prepared = df.copy()

        # Rileva e correggi colonne con tipi misti
        prepared = self._fix_mixed_type_columns(prepared)

        # Converti GeoSeries in WKT
        try:
            import geopandas as gpd
            for col in prepared.columns:
                if isinstance(prepared[col], gpd.GeoSeries):
                    prepared[col] = prepared[col].apply(
                        lambda g: g.wkt if g is not None and not pd.isna(g) else None
                    )
        except ImportError:
            pass

        return prepared

    def _fix_mixed_type_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rileva colonne con tipi misti e le forza a str per evitare errori SQL.
        
        Caso tipico: colonna 'numero' con valori 95, 100, "26A" — pandas la tiene
        come object ma il DDLGenerator potrebbe mapparla a numeric guardando i primi valori.
        Forziamo tutte le colonne object con valori misti a str esplicito.
        """
        for col in df.columns:
            if df[col].dtype != 'object':
                continue
            
            # Colonna object: verifica se contiene mix di tipi
            non_null = df[col].dropna()
            if len(non_null) == 0:
                continue
            
            # Campiona per performance (max 1000 righe)
            sample = non_null.head(1000)
            has_numeric = False
            has_string = False
            
            for val in sample:
                if isinstance(val, (int, float)):
                    has_numeric = True
                elif isinstance(val, str):
                    has_string = True
                if has_numeric and has_string:
                    break
            
            if has_numeric and has_string:
                # Colonna mista: converti tutto a str per sicurezza
                logger.info(
                    f"Colonna '{col}' contiene tipi misti (numeri + stringhe), "
                    f"conversione a TEXT"
                )
                df[col] = df[col].apply(lambda v: str(v) if v is not None and not pd.isna(v) else None)
        
        return df

    def _geometry_to_wkt(self, value) -> Optional[str]:
        """Converte un valore geometria in WKT."""
        if value is None:
            return None

        # Già stringa WKT
        if isinstance(value, str):
            val_upper = value.strip().upper()
            if any(val_upper.startswith(prefix) for prefix in (
                "POINT", "LINESTRING", "POLYGON", "MULTI", "GEOMETRY"
            )):
                return value.strip()
            # Stringa GeoJSON -> WKT tramite shapely
            if value.strip().startswith("{"):
                try:
                    import json as _json
                    from shapely.geometry import shape
                    return shape(_json.loads(value)).wkt
                except Exception:
                    pass
            # Potrebbe essere WKB hex
            try:
                from shapely import wkb
                geom = wkb.loads(bytes.fromhex(value))
                return geom.wkt
            except Exception:
                return value

        # Shapely geometry
        if hasattr(value, 'wkt'):
            return value.wkt

        # Fallback
        return str(value)

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
        except Exception:
            # Non critico, procedi comunque
            pass