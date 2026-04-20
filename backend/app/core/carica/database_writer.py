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
from app.config import settings


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
        try:
            exists = self.table_exists()
        except Exception as e:
            return False, (
                f"Verifica esistenza tabella "
                f"{self.config.schema_name}.{self.config.table_name} "
                f"fallita: {str(e)}"
            )

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
            try:
                existing_col_names = {c["name"] for c in self.get_existing_columns()}
            except Exception as e:
                return False, (
                    f"Lettura colonne tabella "
                    f"{self.config.schema_name}.{self.config.table_name} "
                    f"fallita: {str(e)}"
                )
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
        """Verifica se la tabella destinazione esiste.

        Solleva l'eccezione sottostante se la query non e' eseguibile
        (connessione, permessi, DDL). Il chiamante deve distinguere
        "tabella non trovata" (False) da "verifica fallita" (raise).
        """
        query, params = self.ddl.query_table_exists(
            self.config.schema_name, self.config.table_name
        )
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), params)
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
            raise

    def get_existing_columns(self) -> List[Dict[str, str]]:
        """Recupera le colonne della tabella destinazione.

        Solleva l'eccezione sottostante se la query non e' eseguibile;
        una lista vuota significa esclusivamente "tabella senza colonne
        o inesistente", mai "introspection fallita".
        """
        query, params = self.ddl.query_table_columns(
            self.config.schema_name, self.config.table_name
        )
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), params)
                return [
                    {"name": row[0], "type": str(row[1])}
                    for row in result
                ]
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero colonne: {e}", exc_info=True)
            raise

    def get_schemas(self) -> List[str]:
        """Recupera la lista degli schema disponibili.

        Solleva l'eccezione sottostante se la query non e' eseguibile;
        una lista vuota significa esclusivamente "nessuno schema
        visibile", mai "introspection fallita".
        """
        query = self.ddl.query_list_schemas()
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))
                return [str(row[0]) for row in result]
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero schema: {e}", exc_info=True)
            raise

    def get_tables(self, schema_name: str) -> List[str]:
        """Recupera la lista delle tabelle in uno schema.

        Solleva l'eccezione sottostante se la query non e' eseguibile;
        una lista vuota significa esclusivamente "schema vuoto", mai
        "introspection fallita".
        """
        query, params = self.ddl.query_list_tables(schema_name)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query), params)
                return [str(row[0]) for row in result]
        except Exception as e:
            logger.error(f"[CRASH] Errore recupero tabelle: {e}", exc_info=True)
            raise

    # ========================================================================
    # GESTIONE TABELLA
    # ========================================================================

    def create_table(
        self,
        columns: List[ColumnMapping],
        pandas_dtypes: Optional[Dict[str, str]] = None,
        max_lengths: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Crea la tabella destinazione.

        Args:
            columns: mappature colonne.
            pandas_dtypes: dtypes reali dal DataFrame per type mapping.
            max_lengths: {col_name: max_char_length} opzionale (C5 fix 2).
                Se passato, su Oracle abilita auto-promote VARCHAR2(4000)
                → CLOB per colonne > 4000 char.
        """
        # Genera DDL con dtypes reali dal DataFrame
        if pandas_dtypes is None:
            pandas_dtypes = {}
        create_sql = self.ddl.generate_create_table(
            self.config.schema_name,
            self.config.table_name,
            columns,
            pandas_dtypes,
            max_lengths=max_lengths,
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

        # C4: avviso difensivo sulla combo rischiosa. Se il write fallisce
        # a meta' con batch gia' committati (PER_BATCH) + write_mode=APPEND
        # + nessun match_columns per conflict detection, un rilancio
        # manuale duplicherebbe le righe gia' scritte. La combo non e'
        # bloccata (puo' essere legittima per tabelle append-only con
        # lineage esterno), ma va loggata esplicitamente per l'operatore.
        if (
            self.config.write_mode == WriteMode.APPEND
            and self.config.transaction_mode == TransactionMode.PER_BATCH
            and not self.config.match_columns
        ):
            logger.warning(
                f"[UNSAFE WRITE CONFIG] Tabella "
                f"{self.config.schema_name}.{self.config.table_name}: "
                f"write_mode=append + transaction_mode=per_batch + nessun "
                f"match_columns configurato. In caso di errore parziale, "
                f"i batch gia' committati restano scritti e un rilancio "
                f"manuale duplicherebbe le righe. Considera: "
                f"(a) transaction_mode=all_or_nothing (rollback completo), "
                f"oppure (b) aggiungere match_columns per conflict detection."
            )

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
                    # C6 critical: REPLACE pre-fix faceva drop_table()
                    # (DDL auto-commit) + create_table() + _batch_insert().
                    # Se l'INSERT falliva, la tabella del cliente non
                    # esisteva piu' → data loss deterministica.
                    # Con _replace_atomic: CREATE shadow + INSERT +
                    # RENAME atomico → target mai esposto a stato
                    # intermedio. Route REPLACE attraverso questo path
                    # e saltiamo l'attuale drop/create/insert in-line.
                    # Calcolo dtypes e max_lengths qui (prima del branch)
                    # cosi' sono disponibili a _replace_atomic.
                    real_dtypes = {
                        col: str(prepared_df[col].dtype)
                        for col in prepared_df.columns
                    }
                    for cm in columns:
                        if cm.source_column in prepared_df.columns:
                            real_dtypes[cm.source_column] = str(
                                prepared_df[cm.source_column].dtype
                            )
                        if cm.dest_column in prepared_df.columns:
                            real_dtypes[cm.dest_column] = str(
                                prepared_df[cm.dest_column].dtype
                            )
                    max_lengths = {}
                    _STRING_DTYPES = ("object", "string", "category", "str")
                    for col_name in prepared_df.columns:
                        col_dtype = str(prepared_df[col_name].dtype).lower()
                        is_string_like = (
                            col_dtype in _STRING_DTYPES
                            or col_dtype.startswith("string")
                        )
                        if is_string_like:
                            try:
                                max_len = int(
                                    prepared_df[col_name].astype(str)
                                    .str.len().max() or 0
                                )
                            except Exception:
                                max_len = 0
                            if max_len > 0:
                                max_lengths[col_name] = max_len

                    result = self._replace_atomic(
                        prepared_df, columns, result, real_dtypes, max_lengths
                    )
                    # Skip il path non-atomic originale (drop+create+insert)
                    result.completed_at = datetime.now(timezone.utc).isoformat()
                    return result
                # CREATE puro (senza if_table_exists=replace): path
                # originale — no drop (se la tabella esiste e
                # if_table_exists=error, il create_table() fallira').
                # Passa i dtypes reali del DataFrame per type mapping corretto
                # Chiavi: sia nome colonna DataFrame che source_column del mapping
                real_dtypes = {col: str(prepared_df[col].dtype) for col in prepared_df.columns}
                for cm in columns:
                    if cm.source_column in prepared_df.columns:
                        real_dtypes[cm.source_column] = str(prepared_df[cm.source_column].dtype)
                    if cm.dest_column in prepared_df.columns:
                        real_dtypes[cm.dest_column] = str(prepared_df[cm.dest_column].dtype)

                # C5 fix 2: calcola max_lengths per colonne string (dtype=object
                # o string) cosi' `generate_create_table` puo' auto-promuovere
                # VARCHAR2(4000) → CLOB su Oracle per colonne lunghe. Per
                # dialetti non-Oracle l'informazione viene ignorata — calcolo
                # comunque one-shot, costo trascurabile su DataFrame gia' in RAM.
                # Dtype check difensivo: `_fix_mixed_type_columns` puo'
                # aver convertito colonne a dtype='str' (non 'object').
                # Aggiungiamo anche 'str' + tutti gli startswith("string")
                # per coprire pandas StringDtype moderno.
                max_lengths = {}
                _STRING_DTYPES = ("object", "string", "category", "str")
                for col_name in prepared_df.columns:
                    col_dtype = str(prepared_df[col_name].dtype).lower()
                    is_string_like = (
                        col_dtype in _STRING_DTYPES
                        or col_dtype.startswith("string")
                    )
                    if is_string_like:
                        try:
                            max_len = int(prepared_df[col_name].astype(str).str.len().max() or 0)
                        except Exception:
                            max_len = 0
                        if max_len > 0:
                            max_lengths[col_name] = max_len

                self.create_table(columns, pandas_dtypes=real_dtypes, max_lengths=max_lengths)
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
                                prepared_df, columns, result
                            )
                        else:
                            logger.info(
                                f"[TRUNCATE_INSERT] {len(prepared_df)} >= "
                                f"{threshold}: uso stage+swap"
                            )
                            result = self._truncate_insert_stage_swap(
                                prepared_df, columns, result
                            )
                    else:
                        result = self._truncate_insert_atomic(
                            prepared_df, columns, result
                        )
                else:
                    # PER_BATCH: semantica "fire-and-commit" dichiarata dal
                    # caller. Accettiamo lo stesso trade-off del flow APPEND
                    # per_batch (vedi WARNING difensivo in write() per la
                    # combo rischiosa).
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
    # TRUNCATE + INSERT ATOMICO (C5 fix 1)
    # ========================================================================

    def _truncate_insert_atomic(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """TRUNCATE + INSERT in UNA transazione (ALL_OR_NOTHING).

        Necessario su Oracle: pre-fix, `truncate_table()` committava la
        sua transazione e poi il `_batch_insert` apriva una nuova
        transazione. Se l'INSERT falliva, il rollback copriva solo
        l'INSERT e la tabella restava vuota → perdita dati.

        Qui usiamo:
        - una sola connection
        - generate_truncate() che su Oracle emette DELETE FROM (DML,
          transazionale) invece di TRUNCATE TABLE (DDL auto-commit)
        - commit finale solo se tutti i batch passano
        - rollback implicito on exception (conn.close() senza commit)
        """
        trunc_sql = self.ddl.generate_truncate(
            self.config.schema_name, self.config.table_name
        )
        insert_sql, _ = self.ddl.generate_insert(
            self.config.schema_name, self.config.table_name, columns,
        )

        active_cols = [c for c in columns if not c.skip]
        batch_size = self.config.batch_size
        total_rows = len(df)
        total_batches = math.ceil(total_rows / batch_size) if total_rows else 0
        result.batches_total = total_batches
        start_time = time.time()

        logger.info(
            f"TRUNCATE+INSERT atomico: {total_rows} righe, "
            f"{total_batches} batch da {batch_size}"
        )

        try:
            with self.engine.connect() as conn:
                if self.db_type == "oracle":
                    self._oracle_setup(conn)

                conn.execute(text(trunc_sql))

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, total_rows)
                    batch_df = df.iloc[start_idx:end_idx]

                    rows_in_batch = self._execute_batch(
                        conn, batch_df, active_cols, insert_sql, "insert"
                    )
                    result.rows_written += rows_in_batch
                    result.batches_completed = batch_num + 1

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
                    f"TRUNCATE+INSERT atomico completato: "
                    f"{result.rows_written}/{result.total_rows} righe"
                )

        except Exception as e:
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"Errore durante truncate+insert: {e}"
            result.error_details = repr(e)
            result.rows_written = 0  # rollback → niente scritto
            logger.error(
                f"[CRASH] Rollback TRUNCATE+INSERT (tabella preserva "
                f"contenuto precedente): {e}", exc_info=True,
            )

        return result

    # ========================================================================
    # STAGE + SWAP (C5 fix 3 — UNDO safety su dataset grandi)
    # ========================================================================

    def _truncate_insert_stage_swap(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """TRUNCATE+INSERT via stage+swap per dataset > soglia.

        Pattern:
        1. Cleanup shadow/backup orfani da run precedenti crashati
        2. CREATE TABLE shadow LIKE target (vuota)
        3. INSERT batch nella shadow (zero pressione su target)
        4. SWAP: target→backup, shadow→target
        5. DROP backup

        Vantaggi:
        - Niente DELETE da milioni di righe → UNDO/log non saturano
        - Target resta intatto fino allo swap → lettori concorrenti OK
        - Rollback naturale: se PHASE 2/3 falliscono, target non e'
          mai stato toccato

        Fallback automatico ad atomic se CREATE TABLE fallisce per
        privilegi insufficienti (WARNING loud nel log).
        """
        src_table = self.config.table_name
        schema = self.config.schema_name
        stg_table = f"{src_table}__stg"
        backup_table = f"{src_table}__bak"

        drop_stg_sql = self.ddl.generate_drop_if_exists(schema, stg_table)
        drop_bak_sql = self.ddl.generate_drop_if_exists(schema, backup_table)
        create_like_sql = self.ddl.generate_create_like(
            schema, src_table, stg_table
        )
        swap_stmts = self.ddl.generate_swap_rename(
            schema, src_table, stg_table
        )
        insert_sql, _ = self.ddl.generate_insert(schema, stg_table, columns)

        total_rows = len(df)
        logger.info(
            f"[STAGE+SWAP] avvio per {src_table}: {total_rows} righe, "
            f"shadow={stg_table}, backup={backup_table}"
        )

        # -------- PHASE 1+2: cleanup orfani + CREATE shadow --------
        mssql_name_mapping: Dict[str, Tuple[str, str]] = {}  # constraint/index suffixed→original (MSSQL only)
        try:
            with self.engine.connect() as conn:
                if self.db_type == "oracle":
                    self._oracle_setup(conn)
                conn.execute(text(drop_stg_sql))
                conn.execute(text(drop_bak_sql))
                conn.execute(text(create_like_sql))
                # MSSQL: CREATE e' SELECT INTO, perde PK/UK/CHECK/indici.
                # Introspect sys catalog + replay con suffisso __stg per
                # evitare collisioni. Post-swap (PHASE 6) rinomineremo
                # via sp_rename per ripristinare i nomi originali.
                if self.db_type == "mssql":
                    mssql_name_mapping = self._mssql_replicate_schema_to_shadow(
                        conn, schema, src_table, stg_table
                    )
                conn.commit()
        except Exception as e:
            msg = str(e).lower()
            privilege_markers = (
                "permission", "privilege", "insufficient", "denied",
                "ora-01031", "ora-00942",  # Oracle
                "42501", "42000",  # Postgres/MySQL SQLSTATE permission
                "command denied",  # MySQL
                "does not exist",
            )
            if any(m in msg for m in privilege_markers):
                logger.warning(
                    f"[STAGE+SWAP] CREATE shadow fallito "
                    f"(privilegi/grant insufficienti): {e}. "
                    f"Fallback automatico ad atomic DELETE+INSERT. "
                    f"Per abilitare stage+swap il DB user necessita di "
                    f"CREATE TABLE + DROP TABLE + RENAME sullo schema."
                )
                return self._truncate_insert_atomic(df, columns, result)
            # Errore non-permission: cleanup shadow e rilancia
            try:
                with self.engine.connect() as conn2:
                    conn2.execute(text(drop_stg_sql))
                    conn2.commit()
            except Exception as cleanup_err:
                logger.debug(
                    "[STAGE+SWAP] cleanup shadow post-CREATE fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"CREATE shadow fallito: {e}"
            result.error_details = repr(e)
            logger.error(f"[STAGE+SWAP] errore non-permission: {e}", exc_info=True)
            return result

        # -------- PHASE 3: INSERT batch nella shadow --------
        active_cols = [c for c in columns if not c.skip]
        batch_size = self.config.batch_size
        total_batches = math.ceil(total_rows / batch_size) if total_rows else 0
        result.batches_total = total_batches
        start_time = time.time()

        try:
            with self.engine.begin() as conn:
                if self.db_type == "oracle":
                    self._oracle_setup(conn)

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, total_rows)
                    batch_df = df.iloc[start_idx:end_idx]

                    # IMPORTANT: target_table=stg_table per il bulk path.
                    # insert_sql qui punta alla shadow `<src>__stg`, non
                    # alla tabella target. Senza questo override il bulk
                    # caricherebbe i dati direttamente sul target,
                    # rompendo la semantica stage+swap (che esiste apposta
                    # per non toccare il target finche' lo shadow e' completo).
                    rows_in_batch = self._execute_batch(
                        conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=stg_table,
                    )
                    result.rows_written += rows_in_batch
                    result.batches_completed = batch_num + 1

                    elapsed = time.time() - start_time
                    self.report_progress(BatchProgress(
                        batch_number=batch_num + 1,
                        total_batches=total_batches,
                        rows_written=result.rows_written,
                        total_rows=result.total_rows,
                        elapsed_seconds=elapsed,
                    ))
        except Exception as e:
            # Shadow pieno a meta'/vuoto: target intatto, cleanup shadow
            logger.error(
                f"[STAGE+SWAP] INSERT shadow fallito, target intatto, "
                f"cleanup shadow: {e}", exc_info=True,
            )
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(drop_stg_sql))
                    conn.commit()
            except Exception as ce:
                logger.warning(
                    f"[STAGE+SWAP] cleanup shadow fallito (non bloccante, "
                    f"la shadow verra' rimossa alla prossima run): {ce}"
                )
            result.rows_written = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"INSERT shadow fallito: {e}"
            result.error_details = repr(e)
            return result

        # -------- PHASE 4: SWAP atomico target<->shadow --------
        try:
            # Postgres/MSSQL: multi-statement DDL in UNA txn.
            # MySQL: singola RENAME TABLE atomica.
            # Oracle: 2 ALTER RENAME DDL (ciascuno auto-commit) → finestra
            # sub-secondo di inconsistenza, documentata.
            if self.db_type in ("postgres", "mssql"):
                with self.engine.begin() as conn:
                    for stmt in swap_stmts:
                        conn.execute(text(stmt))
            else:
                with self.engine.connect() as conn:
                    for stmt in swap_stmts:
                        conn.execute(text(stmt))
                        conn.commit()
        except Exception as e:
            logger.error(
                f"[STAGE+SWAP] SWAP fallito: {e}", exc_info=True,
            )
            # Recovery su Oracle: se il primo rename (src→backup) e' passato
            # ma il secondo (stg→src) no, il target e' SPARITO. Tentiamo
            # di rinominare backup→src per ripristinare lo stato.
            if self.db_type == "oracle":
                try:
                    restore_sql = (
                        f'ALTER TABLE "{schema.upper()}"."{backup_table.upper()}" '
                        f'RENAME TO "{src_table.upper()}"'
                    )
                    with self.engine.connect() as conn:
                        conn.execute(text(restore_sql))
                        conn.commit()
                    logger.info(
                        f"[STAGE+SWAP] recovery Oracle: backup ripristinato "
                        f"come target {src_table}"
                    )
                except Exception as re_err:
                    logger.error(
                        f"[STAGE+SWAP] RECOVERY FALLITA — target {src_table} "
                        f"potrebbe essere MANCANTE. Backup: {backup_table}. "
                        f"Intervento manuale richiesto. Errore: {re_err}"
                    )
            result.rows_written = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"SWAP fallito: {e}"
            result.error_details = repr(e)
            return result

        # -------- PHASE 5: DROP backup (best-effort) --------
        try:
            with self.engine.connect() as conn:
                conn.execute(text(drop_bak_sql))
                conn.commit()
        except Exception as e:
            logger.warning(
                f"[STAGE+SWAP] drop backup {backup_table} fallito "
                f"(orfano lasciato, verra' rimosso alla prossima run): {e}"
            )

        # -------- PHASE 6 (MSSQL only): ripristina nomi originali --------
        # Dopo il DROP bak i nomi costraint/indice originali sono liberi,
        # quindi usiamo sp_rename per spogliare il suffisso __stg e
        # ottenere uno schema identico a quello pre-swap.
        # Qualifier diverso per OBJECT (schema.name) vs INDEX (schema.table.name):
        # i constraint sono schema-level objects, gli indici sono table-level.
        if self.db_type == "mssql" and mssql_name_mapping:
            try:
                with self.engine.connect() as conn:
                    for suffixed, (original, kind) in mssql_name_mapping.items():
                        if kind == "INDEX":
                            qualified_old = f"{schema}.{src_table}.{suffixed}"
                        else:  # OBJECT (constraint)
                            qualified_old = f"{schema}.{suffixed}"
                        conn.execute(text(
                            f"EXEC sp_rename '{qualified_old}', "
                            f"'{original}', '{kind}'"
                        ))
                    conn.commit()
            except Exception as e:
                logger.warning(
                    f"[STAGE+SWAP MSSQL] rename constraint post-swap fallito "
                    f"(nomi con suffisso __stg restano, ma schema funzionale "
                    f"preservato): {e}"
                )

        result.status = WriteStatus.SUCCESS
        logger.info(
            f"[STAGE+SWAP] completato: {result.rows_written}/{total_rows} "
            f"righe swappate in {time.time() - start_time:.1f}s"
        )
        return result

    # ========================================================================
    # REPLACE ATOMICO (C6 critical fix: drop+create+insert → stage+swap)
    # ========================================================================

    def _replace_atomic(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
        pandas_dtypes: Dict[str, str],
        max_lengths: Dict[str, int],
    ) -> WriteResult:
        """REPLACE via stage+swap atomico.

        Pre-fix: drop_table() + create_table() + _batch_insert() sono
        3 operazioni non-atomic. Se l'INSERT fallisce, il target del
        cliente non esiste piu' (DROP gia' committato) → data loss
        deterministica, peggiore di TRUNCATE_INSERT pre-C5.

        Post-fix: CREATE shadow con schema dai column_mappings, INSERT
        batch, RENAME atomico target→backup + shadow→target, DROP bak.
        Se INSERT fallisce, target resta nello stato precedente
        (nuovi dati mai esposti, schema vecchio preservato).

        Casi:
        - target esiste: full swap (2 rename + drop bak)
        - target non esiste: singolo rename stg → target
        """
        src_table = self.config.table_name
        schema = self.config.schema_name
        stg_table = f"{src_table}__stg"
        backup_table = f"{src_table}__bak"

        logger.info(
            f"[REPLACE ATOMIC] avvio per {src_table}: {len(df)} righe, "
            f"shadow={stg_table}"
        )

        # Cleanup difensivo orfani da run crashed
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    self.ddl.generate_drop_if_exists(schema, stg_table)
                ))
                conn.execute(text(
                    self.ddl.generate_drop_if_exists(schema, backup_table)
                ))
                conn.commit()
        except Exception as cleanup_err:
            logger.debug(
                "[REPLACE ATOMIC] cleanup difensivo orfani stg/bak fallito, ignoro: %s: %s",
                type(cleanup_err).__name__,
                cleanup_err,
            )

        # Check se target esiste (decide se swap vs single rename)
        target_exists = self._table_exists_for(schema, src_table)

        # -------- PHASE 1: CREATE shadow con schema fresh --------
        try:
            create_sql = self.ddl.generate_create_table(
                schema, stg_table, columns, pandas_dtypes or {},
                max_lengths=max_lengths or {},
            )
            with self.engine.connect() as conn:
                conn.execute(text(create_sql))
                conn.commit()
        except Exception as e:
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"CREATE shadow fallito: {e}"
            result.error_details = repr(e)
            logger.error(
                f"[REPLACE ATOMIC] CREATE shadow fallito: {e}", exc_info=True
            )
            return result

        # -------- PHASE 2: INSERT batch --------
        insert_sql, _ = self.ddl.generate_insert(schema, stg_table, columns)
        active_cols = [c for c in columns if not c.skip]
        batch_size = self.config.batch_size
        total_rows = len(df)
        total_batches = math.ceil(total_rows / batch_size) if total_rows else 0
        result.batches_total = total_batches
        start_time = time.time()

        try:
            with self.engine.begin() as conn:
                if self.db_type == "oracle":
                    self._oracle_setup(conn)

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, total_rows)
                    batch_df = df.iloc[start_idx:end_idx]

                    # target_table=stg_table: insert_sql punta alla shadow
                    # `__stg`, il bulk path deve scrivere lì, non sul target.
                    rows = self._execute_batch(
                        conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=stg_table,
                    )
                    result.rows_written += rows
                    result.batches_completed = batch_num + 1

                    self.report_progress(BatchProgress(
                        batch_number=batch_num + 1,
                        total_batches=total_batches,
                        rows_written=result.rows_written,
                        total_rows=result.total_rows,
                        elapsed_seconds=time.time() - start_time,
                    ))
        except Exception as e:
            logger.error(
                f"[REPLACE ATOMIC] INSERT shadow fallito, target intatto, "
                f"cleanup shadow: {e}", exc_info=True,
            )
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(
                        self.ddl.generate_drop_if_exists(schema, stg_table)
                    ))
                    conn.commit()
            except Exception as cleanup_err:
                logger.debug(
                    "[REPLACE ATOMIC] cleanup shadow post-INSERT fail fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )
            result.rows_written = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"INSERT shadow fallito: {e}"
            result.error_details = repr(e)
            return result

        # -------- PHASE 3: SWAP rename --------
        try:
            if target_exists:
                swap_stmts = self.ddl.generate_swap_rename(
                    schema, src_table, stg_table
                )
            else:
                # Target non esisteva: singolo rename stg → target
                swap_stmts = self._generate_single_rename(
                    schema, stg_table, src_table
                )

            if self.db_type in ("postgres", "mssql"):
                with self.engine.begin() as conn:
                    for stmt in swap_stmts:
                        conn.execute(text(stmt))
            else:
                with self.engine.connect() as conn:
                    for stmt in swap_stmts:
                        conn.execute(text(stmt))
                        conn.commit()
        except Exception as e:
            logger.error(
                f"[REPLACE ATOMIC] SWAP fallito: {e}", exc_info=True,
            )
            # Recovery Oracle se target esisteva + primo rename passato
            if self.db_type == "oracle" and target_exists:
                try:
                    restore_sql = (
                        f'ALTER TABLE "{schema.upper()}"."{backup_table.upper()}" '
                        f'RENAME TO "{src_table.upper()}"'
                    )
                    with self.engine.connect() as conn:
                        conn.execute(text(restore_sql))
                        conn.commit()
                    logger.info(
                        f"[REPLACE ATOMIC] recovery Oracle: backup "
                        f"ripristinato come target"
                    )
                except Exception as re_err:
                    logger.error(
                        f"[REPLACE ATOMIC] RECOVERY FALLITA, target "
                        f"potrebbe mancare. Intervento manuale: {re_err}"
                    )
            result.rows_written = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"SWAP fallito: {e}"
            return result

        # -------- PHASE 4: DROP backup (se target esisteva) --------
        if target_exists:
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(
                        self.ddl.generate_drop_if_exists(schema, backup_table)
                    ))
                    conn.commit()
            except Exception as e:
                logger.warning(
                    f"[REPLACE ATOMIC] drop backup fallito (orfano, "
                    f"cleanup alla prossima run): {e}"
                )

        result.status = WriteStatus.SUCCESS
        logger.info(
            f"[REPLACE ATOMIC] completato: {result.rows_written}/"
            f"{total_rows} righe in {time.time() - start_time:.1f}s "
            f"(atomic, target mai esposto a stato intermedio)"
        )
        return result

    def _table_exists_for(self, schema: str, table: str) -> bool:
        """table_exists per una coppia (schema, table) arbitraria
        (a differenza di self.table_exists che usa self.config)."""
        try:
            query, params = self.ddl.query_table_exists(schema, table)
            with self.engine.connect() as conn:
                res = conn.execute(text(query), params).scalar()
                if res is None:
                    return False
                try:
                    return int(res) > 0
                except (ValueError, TypeError):
                    return bool(res)
        except Exception as e:
            logger.warning(
                f"[TABLE_EXISTS_FOR] verifica {schema}.{table} fallita, "
                f"assumo False: {e}"
            )
            return False

    def _generate_single_rename(
        self, schema: str, old_name: str, new_name: str
    ) -> List[str]:
        """Rename dialect-specific di UNA tabella (non swap a 3-vie)."""
        old_full = self.ddl.qualified_table_name(schema, old_name)

        if self.db_type == "postgres":
            return [f'ALTER TABLE {old_full} RENAME TO "{new_name}"']
        if self.db_type == "oracle":
            return [
                f'ALTER TABLE {old_full} RENAME TO "{new_name.upper()}"'
            ]
        if self.db_type in ("mysql", "mariadb"):
            new_full = self.ddl.qualified_table_name(schema, new_name)
            return [f"RENAME TABLE {old_full} TO {new_full}"]
        if self.db_type == "mssql":
            qualified_old = f"{schema}.{old_name}" if schema else old_name
            return [f"EXEC sp_rename '{qualified_old}', '{new_name}'"]
        raise ValueError(
            f"_generate_single_rename non supportato per db_type="
            f"{self.db_type}"
        )

    # ========================================================================
    # MSSQL schema introspection + replay (C6 fix: schema preservation)
    # ========================================================================

    def _mssql_replicate_schema_to_shadow(
        self, conn, schema: str, src_table: str, stg_table: str,
    ) -> Dict[str, Tuple[str, str]]:
        """MSSQL: `SELECT INTO ... WHERE 1=0` copia solo colonne; PK, UK,
        CHECK e indici NON sono preservati. Introspect sys.* del target
        e applica tutto sulla shadow con suffisso `__stg` per evitare
        collisione nomi (i nomi originali sono ancora in uso sul target
        fino al rename swap finale).

        Ritorna un mapping {suffixed_name → (original_name, object_kind)}
        usato da PHASE 6 post-swap per spogliare i suffissi via
        sp_rename e ottenere uno schema identico a quello pre-swap.

        Scope di preservazione (scelta pragmatica):
        - PK, UK, CHECK constraints ✓
        - Indexes unique e non-unique ✓
        - FK constraints in uscita — lasciato fuori (rari su tabelle
          di destinazione ETL; se necessari il cliente li ricrea)
        - Triggers — fuori scope (rari su ETL targets)
        - DEFAULT constraints — fuori scope (gestiti a livello colonna
          via CREATE TABLE... DEFAULT, che SELECT INTO non copia; se
          necessario il cliente usa CREATE mode o li aggiunge a mano)
        """
        suffix = "__stg"
        mapping: Dict[str, Tuple[str, str]] = {}

        # ---- PK ----
        pk_rows = conn.execute(text("""
            SELECT kc.name AS cname,
                   STRING_AGG(QUOTENAME(c.name), ',')
                     WITHIN GROUP (ORDER BY ic.key_ordinal) AS cols
            FROM sys.key_constraints kc
            JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id
                                     AND ic.index_id  = kc.unique_index_id
            JOIN sys.columns c ON c.object_id = ic.object_id
                              AND c.column_id = ic.column_id
            JOIN sys.objects o ON kc.parent_object_id = o.object_id
            WHERE kc.type = 'PK'
              AND o.schema_id = SCHEMA_ID(:sch)
              AND o.name = :tbl
            GROUP BY kc.name
        """), {"sch": schema, "tbl": src_table}).fetchall()

        for row in pk_rows:
            orig_name = row[0]
            cols = row[1]
            new_name = f"{orig_name}{suffix}"
            conn.execute(text(
                f"ALTER TABLE [{schema}].[{stg_table}] "
                f"ADD CONSTRAINT [{new_name}] PRIMARY KEY ({cols})"
            ))
            mapping[new_name] = (orig_name, "OBJECT")

        # ---- UK ----
        uk_rows = conn.execute(text("""
            SELECT kc.name AS cname,
                   STRING_AGG(QUOTENAME(c.name), ',')
                     WITHIN GROUP (ORDER BY ic.key_ordinal) AS cols
            FROM sys.key_constraints kc
            JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id
                                     AND ic.index_id  = kc.unique_index_id
            JOIN sys.columns c ON c.object_id = ic.object_id
                              AND c.column_id = ic.column_id
            JOIN sys.objects o ON kc.parent_object_id = o.object_id
            WHERE kc.type = 'UQ'
              AND o.schema_id = SCHEMA_ID(:sch)
              AND o.name = :tbl
            GROUP BY kc.name
        """), {"sch": schema, "tbl": src_table}).fetchall()

        for row in uk_rows:
            orig_name = row[0]
            cols = row[1]
            new_name = f"{orig_name}{suffix}"
            conn.execute(text(
                f"ALTER TABLE [{schema}].[{stg_table}] "
                f"ADD CONSTRAINT [{new_name}] UNIQUE ({cols})"
            ))
            mapping[new_name] = (orig_name, "OBJECT")

        # ---- CHECK ----
        ck_rows = conn.execute(text("""
            SELECT cc.name AS cname, cc.definition AS def
            FROM sys.check_constraints cc
            JOIN sys.objects o ON cc.parent_object_id = o.object_id
            WHERE o.schema_id = SCHEMA_ID(:sch) AND o.name = :tbl
        """), {"sch": schema, "tbl": src_table}).fetchall()

        for row in ck_rows:
            orig_name = row[0]
            definition = row[1]  # e.g. "([amount]>=(0))"
            new_name = f"{orig_name}{suffix}"
            conn.execute(text(
                f"ALTER TABLE [{schema}].[{stg_table}] "
                f"ADD CONSTRAINT [{new_name}] CHECK {definition}"
            ))
            mapping[new_name] = (orig_name, "OBJECT")

        # ---- Non-PK, Non-UK indexes ----
        idx_rows = conn.execute(text("""
            SELECT i.name AS iname,
                   i.is_unique AS uq,
                   STRING_AGG(QUOTENAME(c.name), ',')
                     WITHIN GROUP (ORDER BY ic.key_ordinal) AS cols
            FROM sys.indexes i
            JOIN sys.index_columns ic ON ic.object_id = i.object_id
                                     AND ic.index_id  = i.index_id
            JOIN sys.columns c ON c.object_id = ic.object_id
                              AND c.column_id = ic.column_id
            JOIN sys.objects o ON i.object_id = o.object_id
            WHERE i.is_primary_key = 0
              AND i.is_unique_constraint = 0
              AND i.type > 0
              AND o.schema_id = SCHEMA_ID(:sch)
              AND o.name = :tbl
              AND ic.is_included_column = 0
            GROUP BY i.name, i.is_unique
        """), {"sch": schema, "tbl": src_table}).fetchall()

        for row in idx_rows:
            orig_name = row[0]
            is_unique = bool(row[1])
            cols = row[2]
            new_name = f"{orig_name}{suffix}"
            unique_clause = "UNIQUE " if is_unique else ""
            conn.execute(text(
                f"CREATE {unique_clause}INDEX [{new_name}] "
                f"ON [{schema}].[{stg_table}] ({cols})"
            ))
            mapping[new_name] = (orig_name, "INDEX")

        return mapping

    # ========================================================================
    # ORACLE DBMS_REDEFINITION (C6 fix: atomicity + schema preservation)
    # ========================================================================

    def _truncate_insert_oracle_redef(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Oracle: TRUNCATE_INSERT via DBMS_REDEFINITION — true atomic swap
        con preservazione completa dello schema (PK, UK, FK, indexes,
        triggers, grants, check constraints).

        Sostituisce il path stage+swap standard (che su Oracle usa
        `CREATE AS SELECT WHERE 1=0` e perde indici/constraint) +
        chiude anche la finestra di inconsistenza sub-ms dei due
        RENAME DDL (FINISH_REDEF_TABLE e' atomico).

        Flow:
        1. CAN_REDEF_TABLE(CONS_USE_PK) — fallback a CONS_USE_ROWID;
           se anche quello fallisce → fallback atomic
        2. CREATE interim AS SELECT * WHERE 1=0 — shell vuota
        3. START_REDEF_TABLE — Oracle copia baseline (cost 2-3x, ma
           garantisce consistency tracking via mview log)
        4. DELETE interim + INSERT batch dati nuovi
        5. COPY_TABLE_DEPENDENTS — replica indici, constraint, trigger
        6. FINISH_REDEF_TABLE — swap atomico
        7. DROP interim (ora contiene i dati vecchi, swap gia' fatto)

        Su qualsiasi fallimento post-START → ABORT_REDEF_TABLE +
        cleanup interim + WriteStatus.WRITE_ERROR.
        Su fallimento pre-START (grant/eligibility) → fallback a
        _truncate_insert_atomic con WARNING loud.
        """
        src = self.config.table_name.upper()
        schema = self.config.schema_name.upper()
        interim = f"{src}__RDF"
        src_qualified = f'"{schema}"."{src}"'
        interim_qualified = f'"{schema}"."{interim}"'

        # Insert SQL targeting the interim table (columns match src)
        insert_sql, _ = self.ddl.generate_insert(schema, interim, columns)

        active_cols = [c for c in columns if not c.skip]
        batch_size = self.config.batch_size
        total_rows = len(df)
        total_batches = math.ceil(total_rows / batch_size) if total_rows else 0
        result.batches_total = total_batches
        start_time = time.time()

        logger.info(
            f"[ORACLE REDEF] avvio per {src}: {total_rows} righe, "
            f"interim={interim}"
        )

        # Cleanup eventuale interim orfana da run crashed
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    self.ddl.generate_drop_if_exists(schema, interim)
                ))
                conn.commit()
        except Exception as cleanup_err:
            logger.debug(
                "[ORACLE REDEF] cleanup difensivo interim orfana fallito, ignoro: %s: %s",
                type(cleanup_err).__name__,
                cleanup_err,
            )

        # -------- PHASE 1: eligibility check --------
        options_flag = None
        for opt in ("CONS_USE_PK", "CONS_USE_ROWID"):
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.CAN_REDEF_TABLE(
        uname => '{schema}',
        tname => '{src}',
        options_flag => DBMS_REDEFINITION.{opt}
    );
END;"""))
                    conn.commit()
                options_flag = opt
                break
            except Exception as e:
                logger.debug(
                    f"[ORACLE REDEF] CAN_REDEF_TABLE({opt}) failed: {e}"
                )
                continue

        if options_flag is None:
            logger.warning(
                f"[ORACLE REDEF] tabella {src} non ammissibile per "
                f"DBMS_REDEFINITION (no PK, ROWID inutilizzabile, o "
                f"privilegio EXECUTE ON DBMS_REDEFINITION mancante). "
                f"Fallback a atomic DELETE+INSERT (stessa transazione, "
                f"no swap, OK per schema-preservation)."
            )
            return self._truncate_insert_atomic(df, columns, result)

        # -------- PHASE 2: CREATE interim shell --------
        try:
            create_sql = (
                f"CREATE TABLE {interim_qualified} AS "
                f"SELECT * FROM {src_qualified} WHERE 1=0"
            )
            with self.engine.connect() as conn:
                conn.execute(text(create_sql))
                conn.commit()
        except Exception as e:
            logger.warning(
                f"[ORACLE REDEF] CREATE interim fallito: {e}. "
                f"Fallback a atomic."
            )
            return self._truncate_insert_atomic(df, columns, result)

        # -------- PHASE 3: START_REDEF_TABLE (copia baseline) --------
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.START_REDEF_TABLE(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}',
        col_mapping => NULL,
        options_flag => DBMS_REDEFINITION.{options_flag}
    );
END;"""))
                conn.commit()
        except Exception as e:
            logger.warning(
                f"[ORACLE REDEF] START_REDEF_TABLE fallito: {e}. "
                f"Fallback a atomic."
            )
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(
                        self.ddl.generate_drop_if_exists(schema, interim)
                    ))
                    conn.commit()
            except Exception as cleanup_err:
                logger.debug(
                    "[ORACLE REDEF] cleanup interim post-START_REDEF fail fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )
            return self._truncate_insert_atomic(df, columns, result)

        # Da qui in poi, qualunque errore richiede ABORT_REDEF
        try:
            # -------- PHASE 4: wipe baseline + INSERT dati nuovi --------
            with self.engine.connect() as conn:
                self._oracle_setup(conn)
                conn.execute(text(f"DELETE FROM {interim_qualified}"))

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, total_rows)
                    batch_df = df.iloc[start_idx:end_idx]

                    # Oracle online_redefinition: insert_sql punta alla
                    # interim `__RDF`. Bulk path su Oracle non e'
                    # implementato (dispatcher solleva NotSupported →
                    # fallback executemany), ma passiamo target_table
                    # comunque per safety futura.
                    rows = self._execute_batch(
                        conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=interim,
                    )
                    result.rows_written += rows
                    result.batches_completed = batch_num + 1

                    self.report_progress(BatchProgress(
                        batch_number=batch_num + 1,
                        total_batches=total_batches,
                        rows_written=result.rows_written,
                        total_rows=result.total_rows,
                        elapsed_seconds=time.time() - start_time,
                    ))

                conn.commit()

            # -------- PHASE 5: COPY_TABLE_DEPENDENTS --------
            with self.engine.connect() as conn:
                conn.execute(text(f"""
DECLARE
    num_errors PLS_INTEGER;
BEGIN
    DBMS_REDEFINITION.COPY_TABLE_DEPENDENTS(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}',
        copy_indexes => DBMS_REDEFINITION.CONS_ORIG_PARAMS,
        copy_triggers => TRUE,
        copy_constraints => TRUE,
        copy_privileges => TRUE,
        ignore_errors => FALSE,
        num_errors => num_errors
    );
END;"""))
                conn.commit()

            # -------- PHASE 6: FINISH_REDEF_TABLE (atomic swap) --------
            with self.engine.connect() as conn:
                conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.FINISH_REDEF_TABLE(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}'
    );
END;"""))
                conn.commit()

            # -------- PHASE 7: DROP interim (contiene i dati vecchi) --------
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(
                        self.ddl.generate_drop_if_exists(schema, interim)
                    ))
                    conn.commit()
            except Exception as e:
                logger.warning(
                    f"[ORACLE REDEF] DROP interim post-swap fallito "
                    f"(orfano con dati vecchi, cleanup al prossimo run): {e}"
                )

            result.status = WriteStatus.SUCCESS
            logger.info(
                f"[ORACLE REDEF] completato: {result.rows_written}/"
                f"{total_rows} righe in {time.time() - start_time:.1f}s "
                f"(atomic via DBMS_REDEFINITION, schema preservato)"
            )
            return result

        except Exception as e:
            logger.error(
                f"[ORACLE REDEF] fallimento post-START: {e}", exc_info=True
            )
            # ABORT_REDEF_TABLE per unlock mview log e ripristino
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(f"""
BEGIN
    DBMS_REDEFINITION.ABORT_REDEF_TABLE(
        uname => '{schema}',
        orig_table => '{src}',
        int_table => '{interim}'
    );
END;"""))
                    conn.commit()
            except Exception as ab_err:
                logger.warning(
                    f"[ORACLE REDEF] ABORT_REDEF_TABLE fallito: {ab_err}"
                )
            # Drop interim
            try:
                with self.engine.connect() as conn:
                    conn.execute(text(
                        self.ddl.generate_drop_if_exists(schema, interim)
                    ))
                    conn.commit()
            except Exception as cleanup_err:
                logger.debug(
                    "[ORACLE REDEF] drop interim post-ABORT fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )

            result.rows_written = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"DBMS_REDEFINITION fallimento: {e}"
            result.error_details = repr(e)
            return result

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

        Strategia (in ordine di preferenza):
        1. **Bulk nativo** (`COPY` Postgres / `LOAD DATA` MySQL/MariaDB):
           solo per `operation == "insert"` su Postgres/MySQL/MariaDB
           senza colonne geometry. 10-100x piu' veloce di executemany
           su dataset grandi. Per MSSQL il boost arriva da
           `fast_executemany=True` impostato sull'engine factory
           (vedi `app/db/query_executor.py:_crea_engine_mssql`).
           Fallback automatico ad executemany su qualsiasi
           `BulkInsertNotSupportedError` (geometry, driver mancante,
           server-side disable, errore COPY non recuperabile).
           Disabilitabile via env `CARICA_BULK_INSERT=off`.

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
                # Colonna mista: converti tutto a str per sicurezza (vectorized)
                logger.info(
                    f"Colonna '{col}' contiene tipi misti (numeri + stringhe), "
                    f"conversione a TEXT"
                )
                mask_null = df[col].isna()
                df[col] = df[col].astype(str)
                df.loc[mask_null, col] = None
        
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
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"GeoJSON→WKT fallito: {e}")
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
        except Exception as e:
            # Non critico, procedi comunque
            logger.debug(
                "[ORACLE] ALTER SESSION SDO_GEOM_METADATA_TABLE fallito, ignoro: %s: %s",
                type(e).__name__,
                e,
            )