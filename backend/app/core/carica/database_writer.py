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
      MRO: DatabaseWriter → DataPrepMixin → SchemaIntrospectionMixin →
      TransactionExecutionMixin → ReplaceStrategiesMixin →
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
            # Errore non classificato come permission: fail-closed.
            # Pre-fix: logger.warning + return True ("logga ma non blocca")
            # lasciava passare errori transient/DDL sconosciuti come se i
            # permessi fossero ok, poi CREATE TABLE vero falliva senza
            # diagnostica utile. Ora la verifica segnala esplicitamente
            # il fallimento al chiamante.
            logger.error(
                f"[CRASH] Verifica permessi CREATE fallita su schema "
                f"'{self.config.schema_name}': {e}",
                exc_info=True,
            )
            return False, (
                f"Verifica permessi CREATE TABLE nello schema "
                f"'{self.config.schema_name}' fallita: {str(e)}"
            )

    # ========================================================================
    # SCHEMA INTROSPECTION
    # ========================================================================

    # Phase 5 B2.2 cutover: cluster SCHEMA_INTROSPECTION (`table_exists`,
    # `get_existing_columns`, `get_schemas`, `get_tables`,
    # `_table_exists_for`, `_compute_schema_hints`) spostato in
    # `app.core.carica.writer_introspection.SchemaIntrospectionMixin`.
    # `DatabaseWriter` eredita dal mixin (vedi class declaration sopra).
    # I 6 metodi restano accessibili via MRO. Vedi
    # `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2.

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
        """Svuota la tabella destinazione.

        Solleva l'eccezione sottostante se TRUNCATE non e' eseguibile
        (permessi, lock, DDL). Pre-fix: ritornava False silenziosamente,
        e il caller in write() PER_BATCH (TRUNCATE_INSERT) procedeva
        comunque con l'INSERT appendendo anziche' truncate-inserting —
        correttezza dati compromessa. Ora il boundary in write() cattura
        e segnala WRITE_ERROR.
        """
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
            raise

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

    def _build_initial_result(
        self, total_rows: int, started_at: str
    ) -> WriteResult:
        """Costruisce lo skeleton del WriteResult prima dell'esecuzione.

        Estratto da `write()` (C4.1 Phase 4) come pura factory: nessuna
        logica di business, nessun side-effect. Mantiene unique-source-of-
        truth per i campi `table_name` e `write_mode` derivati da
        `self.config`, evitando duplicazioni inline nei branch di write.
        """
        return WriteResult(
            started_at=started_at,
            total_rows=total_rows,
            table_name=f"{self.config.schema_name}.{self.config.table_name}",
            write_mode=self.config.write_mode.value,
        )

    def _assert_safe_write_config(self) -> None:
        """F-DATA-08 / P1-05: gate fail-loud sulla combo rischiosa.

        Se il write fallisce a meta' con batch gia' committati (PER_BATCH)
        + write_mode=APPEND + nessun match_columns per conflict detection,
        un rilancio manuale duplicherebbe le righe gia' scritte.
        Pre-fix la combo era solo "warning"-livel: in produzione il
        warning e' di fatto silent → corruzione dati su retry partial.
        Ora la combo e' rifiutata fail-loud, opt-in tramite
        `WriteConfig.allow_unsafe_append=True` per chi accetta
        consapevolmente il rischio (es. tabelle append-only con
        lineage esterno che gestisce dedup downstream).

        Estratto da `write()` (C4.1 Phase 4) come helper privato.
        Comportamento bit-per-bit invariato: stessa condizione, stesso
        messaggio di errore, stesso warning su opt-in.
        """
        if not (
            self.config.write_mode == WriteMode.APPEND
            and self.config.transaction_mode == TransactionMode.PER_BATCH
            and not self.config.match_columns
        ):
            return
        if not self.config.allow_unsafe_append:
            raise ValueError(
                f"[UNSAFE WRITE CONFIG] Tabella "
                f"{self.config.schema_name}.{self.config.table_name}: "
                f"combo non sicura write_mode=append + "
                f"transaction_mode=per_batch + nessun match_columns. "
                f"In caso di errore parziale, i batch gia' committati "
                f"restano scritti e un rilancio duplicherebbe le righe. "
                f"Scegli una delle alternative: "
                f"(1) usare transaction_mode=all_or_nothing per rollback "
                f"completo; "
                f"(2) configurare match_columns per conflict detection; "
                f"(3) settare allow_unsafe_append=true consapevolmente "
                f"se la tabella e' append-only con lineage esterno."
            )
        # Opt-in: warning informativo per l'operatore, write procede.
        logger.warning(
            f"[UNSAFE WRITE CONFIG opt-in] Tabella "
            f"{self.config.schema_name}.{self.config.table_name}: "
            f"write_mode=append + transaction_mode=per_batch + "
            f"nessun match_columns + allow_unsafe_append=True. "
            f"Retry parziali possono duplicare righe."
        )

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