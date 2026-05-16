"""
Mixin REPLACE_STRATEGIES — strategie replace/truncate per DatabaseWriter.

Keeps database loading behavior isolated from orchestration callers.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from app.config import settings
from app.core.carica.models import (
    ColumnMapping,
    WriteResult,
    WriteStatus,
)
from app.core.carica.writer_oracle_redef import execute_oracle_redefinition
from app.core.carica.writer_batch_loop import run_batched_inserts
from app.shared.contracts.payloads import IntMap, StringMap
from app.utils.logger import get_logger, get_request_id, get_user_id

# Keep the canonical writer logger name so existing handlers receive mixin logs.
logger = get_logger("app.core.carica.writer")
MssqlNameMapping = Dict[str, Tuple[str, str]]

# Telemetry emits (event=stage_swap.dispatched, ecc.) vanno via stdlib
# logger con stesso name: i campi `extra` diventano attributi del
# LogRecord, cosa che StructuredLogger (wrapper) non supporta di default
_telemetry_logger = logging.getLogger("app.core.carica.writer")


class ReplaceStrategiesMixin:
    """Mixin per `DatabaseWriter`: strategie replace/truncate (atomic /
    stage+swap / oracle redef / replace_atomic) + 2 helper privati
    intra-cluster (`_mssql_replicate_schema_to_shadow`,
    `_generate_single_rename`).

    Le transaction boundaries (`engine.begin` per swap/INSERT batch,
    `engine.connect` per cleanup/DDL singoli) sono incapsulate qui.
    """

    def _truncate_insert_atomic(
        self,
        df: Any,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """TRUNCATE + INSERT in UNA transazione (ALL_OR_NOTHING).

        Necessario su Oracle per mantenere truncate/delete e insert nella
        stessa transazione.

        Qui usiamo:
        - una sola connection
        - generate_truncate che su Oracle emette DELETE FROM (DML,
          transazionale) invece di TRUNCATE TABLE (DDL auto-commit)
        - commit finale solo se tutti i
        - rollback implicito on exception (conn.close senza commit)
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

                def _process_batch(batch_df, batch_num, _conn=conn):
                    rows_in_batch = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert"
                    )
                    result.rows_written += rows_in_batch
                    result.rows_inserted += rows_in_batch
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
            result.rows_inserted = 0
            result.rows_updated = 0
            logger.error(
                f"[CRASH] Rollback TRUNCATE+INSERT (tabella preserva "
                f"contenuto precedente): {e}", exc_info=True,
            )

        return result

    def _truncate_insert_stage_swap(
        self,
        df: Any,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """TRUNCATE+INSERT via stage+swap per dataset > soglia.

        Pattern:
        1. Cleanup shadow/backup orfani da run precedenti crashati
        2. CREATE TABLE shadow LIKE target (vuota)
        3. INSERT shadow (zero pressione su target)
        4. SWAP: target→backup, shadow→target
        5. DROP backup

        Vantaggi:
        - Niente DELETE da milioni di righe → UNDO/log non saturano
        - Target resta intatto fino allo swap → lettori concorrenti OK
        - Rollback naturale: se /3 falliscono, target non e'
          mai stato toccato

        Fallback automatico ad atomic se CREATE TABLE fallisce per
        privilegi insufficienti (WARNING loud nel log).
        """
        src_table = self.config.table_name
        schema = self.config.schema_name
        stg_table = f"{src_table}__stg"
        backup_table = f"{src_table}__bak"

        _telemetry_logger.info(
            f"Stage+swap dispatch: dialect={self.db_type} "
            f"table={schema + '.' if schema else ''}{src_table} "
            f"rows={len(df)}",
            extra={
                "event": "stage_swap.dispatched",
                "dialect": self.db_type,
                "path": "stage_swap",
                "schema": schema,
                "table": src_table,
                "rows": len(df),
                "threshold": int(settings.CARICA_STAGE_SWAP_THRESHOLD or 0),
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )

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

        mssql_name_mapping: MssqlNameMapping = {}
        try:
            with self.engine.connect() as conn:
                if self.db_type == "oracle":
                    self._oracle_setup(conn)
                conn.execute(text(drop_stg_sql))
                conn.execute(text(drop_bak_sql))
                conn.execute(text(create_like_sql))
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
                logger.warning(
                    "[STAGE+SWAP] cleanup shadow post-CREATE fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"CREATE shadow fallito: {e}"
            result.error_details = repr(e)
            logger.error(f"[STAGE+SWAP] errore non-permission: {e}", exc_info=True)
            return result

        active_cols = [c for c in columns if not c.skip]
        batch_size = self.config.batch_size
        total_batches = math.ceil(total_rows / batch_size) if total_rows else 0
        result.batches_total = total_batches
        start_time = time.time()

        try:
            with self.engine.begin() as conn:
                if self.db_type == "oracle":
                    self._oracle_setup(conn)

                def _process_batch(batch_df, batch_num, _conn=conn):
                    # NOTE: target_table=stg_table per il bulk path
                    # insert_sql qui punta alla shadow `<src>__stg`, non
                    # alla tabella target. Senza questo override il bulk
                    # caricherebbe i dati direttamente sul target
                    # rompendo la semantica stage+swap (che esiste apposta
                    # per non toccare il target finche' lo shadow e' completo
                    rows_in_batch = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=stg_table,
                    )
                    result.rows_written += rows_in_batch
                    result.rows_inserted += rows_in_batch
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
            result.rows_inserted = 0
            result.rows_updated = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"INSERT shadow fallito: {e}"
            result.error_details = repr(e)
            return result

        try:
            # Postgres/MSSQL: multi-statement DDL in UNA txn
            # MySQL: singola RENAME TABLE atomica
            # Oracle: 2 ALTER RENAME DDL (ciascuno auto-commit) → finestra
            # sub-secondo di inconsistenza, documentata
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
            # di rinominare backup→src per ripristinare lo stato
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
            result.rows_inserted = 0
            result.rows_updated = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"SWAP fallito: {e}"
            result.error_details = repr(e)
            return result

        try:
            with self.engine.connect() as conn:
                conn.execute(text(drop_bak_sql))
                conn.commit()
        except Exception as e:
            logger.warning(
                f"[STAGE+SWAP] drop backup {backup_table} fallito "
                f"(orfano lasciato, verra' rimosso alla prossima run): {e}"
            )

        # Dopo il DROP bak i nomi costraint/indice originali sono liberi
        # quindi usiamo sp_rename per spogliare il suffisso __stg e
        # ottenere uno schema identico a quello pre-swap
        # Qualifier diverso per OBJECT (schema.name) vs INDEX (schema.table.name
        # i constraint sono schema-level objects, gli indici sono table-level
        if self.db_type == "mssql" and mssql_name_mapping:
            try:
                with self.engine.connect() as conn:
                    for suffixed, (original, kind) in mssql_name_mapping.items():
                        if kind == "INDEX":
                            qualified_old = f"{schema}.{src_table}.{suffixed}"
                        else:  # OBJECT (constraint
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

    def _replace_atomic(
        self,
        df: Any,
        columns: List[ColumnMapping],
        result: WriteResult,
        pandas_dtypes: StringMap,
        max_lengths: IntMap,
    ) -> WriteResult:
        """REPLACE via stage+swap atomico.

        CREATE shadow con schema dai column_mappings, INSERT batch,
        RENAME atomico target→backup + shadow→target, DROP bak.
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
            logger.warning(
                "[REPLACE ATOMIC] cleanup difensivo orfani stg/bak fallito, ignoro: %s: %s",
                type(cleanup_err).__name__,
                cleanup_err,
            )

        # Check se target esiste (decide se swap vs single rename
        target_exists = self._table_exists_for(schema, src_table)

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

                def _process_batch(batch_df, batch_num, _conn=conn):
                    # target_table=stg_table: insert_sql punta alla shadow
                    # `__stg`, il bulk path deve scrivere lì, non sul target
                    rows = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=stg_table,
                    )
                    result.rows_written += rows
                    result.rows_inserted += rows
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
                logger.warning(
                    "[REPLACE ATOMIC] cleanup shadow post-INSERT fail fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )
            result.rows_written = 0
            result.rows_inserted = 0
            result.rows_updated = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"INSERT shadow fallito: {e}"
            result.error_details = repr(e)
            return result

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
                        "[REPLACE ATOMIC] recovery Oracle: backup "
                        "ripristinato come target"
                    )
                except Exception as re_err:
                    logger.error(
                        f"[REPLACE ATOMIC] RECOVERY FALLITA, target "
                        f"potrebbe mancare. Intervento manuale: {re_err}"
                    )
            result.rows_written = 0
            result.rows_inserted = 0
            result.rows_updated = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"SWAP fallito: {e}"
            return result

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

    def _mssql_replicate_schema_to_shadow(
        self, conn, schema: str, src_table: str, stg_table: str,
    ) -> MssqlNameMapping:
        """MSSQL: `SELECT INTO ... WHERE 1=0` copia solo colonne; PK, UK,
        CHECK e indici NON sono preservati. Introspect sys.* del target
        e applica tutto sulla shadow con suffisso `__stg` per evitare
        collisione nomi (i nomi originali sono ancora in uso sul target
        fino al rename swap finale).

        Ritorna un mapping {suffixed_name → (original_name, object_kind)}
        usato da post-swap per spogliare i suffissi via
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
        mapping: MssqlNameMapping = {}

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

    def _truncate_insert_oracle_redef(
        self,
        df: Any,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Dispatch Oracle online redefinition to the dedicated module."""
        return execute_oracle_redefinition(self, df, columns, result)
