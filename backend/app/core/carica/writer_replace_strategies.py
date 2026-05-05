"""
Mixin REPLACE_STRATEGIES — strategie replace/truncate per DatabaseWriter.

Origine
=======
Estratto da `app.core.carica.database_writer.DatabaseWriter` come parte
di Phase 5 Batch B2.4b (cluster split + mixin pattern, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2 + §6.1 facade pattern
ratificato in B1; mixin pattern ratificato in B2.1+B2.2+B2.3; AST
contract modernizzato in B2.4a).

Razionale del raggruppamento: 6 metodi che insieme implementano tutte
le strategie di scrittura "replace/truncate" (cancellazione +
reinserimento atomico) supportate dal `DatabaseWriter`, condividendo
il pattern shadow/swap per garantire atomicita' e schema preservation:

  - `_truncate_insert_atomic`            — TRUNCATE + INSERT in UNA
                                            transazione (ALL_OR_NOTHING).
                                            Path piu' semplice; usato come
                                            fallback dagli altri 3 path
                                            quando i privilegi/eligibility
                                            non sono disponibili.
  - `_truncate_insert_stage_swap`        — pattern stage+swap cross-DB
                                            per dataset > soglia: shadow
                                            CREATE LIKE + INSERT batch +
                                            RENAME swap atomico + DROP bak.
                                            MSSQL ha replay schema custom
                                            (PHASE 6 sp_rename).
  - `_truncate_insert_oracle_redef`      — Oracle DBMS_REDEFINITION:
                                            true atomic swap con preservazione
                                            completa di PK/UK/FK/indexes/
                                            triggers/grants. Sostituisce
                                            stage+swap su Oracle quando il
                                            DB e' eligible.
  - `_replace_atomic`                    — REPLACE via stage+swap atomico:
                                            CREATE shadow + INSERT batch +
                                            RENAME swap (full o single).
                                            Caso target-non-esiste gestito
                                            via singolo rename.
  - `_mssql_replicate_schema_to_shadow`  — helper privato di
                                            `_truncate_insert_stage_swap`
                                            per MSSQL: introspetta
                                            sys.* del target e replica
                                            PK/UK/CHECK/INDEX sulla shadow
                                            con suffisso `__stg`.
  - `_generate_single_rename`            — helper privato di
                                            `_replace_atomic` per il caso
                                            target-non-esiste: rename
                                            dialect-specific di una sola
                                            tabella.

Transaction boundaries
======================
**IDENTICHE al pre-extraction**, copiate bit-per-bit:

- `_truncate_insert_atomic`: 1 `engine.connect()` + commit/rollback
  manuali con conn.close() implicito (no commit → rollback).
- `_truncate_insert_stage_swap`: 6 `engine.connect()` (cleanup + create
  shadow + cleanup post-fail + drop bak + MSSQL rename + recovery
  Oracle) + 2 `engine.begin()` (INSERT + SWAP per Postgres/MSSQL).
- `_replace_atomic`: 6 `engine.connect()` (cleanup + create + cleanup
  post-INSERT-fail + drop bak + recovery Oracle + drop bak finale)
  + 2 `engine.begin()` (INSERT + SWAP per Postgres/MSSQL).
- `_truncate_insert_oracle_redef`: ~7 `engine.connect()` (cleanup +
  CAN_REDEF + CREATE interim + START_REDEF + INSERT + COPY_DEPENDENTS +
  FINISH_REDEF + DROP interim + ABORT_REDEF + drop interim post-ABORT).
  Tutti commit/rollback manuali, no `engine.begin()`.
- `_mssql_replicate_schema_to_shadow`: nessuna tx propria — riceve
  `conn` dal caller (`_truncate_insert_stage_swap`), fa solo
  `conn.execute()` + introspect.
- `_generate_single_rename`: nessuna tx, pure SQL string generation.

Cleanup guards (test contract B2.0 #5 modernizzato)
===================================================
6 cleanup guards distribuite su 3 metodi, verificate da
`inspect.getsource(DatabaseWriter._method)` post-modernization:

  - `_truncate_insert_stage_swap`: 1 stringa
    `[STAGE+SWAP] cleanup shadow post-CREATE fallito`
  - `_replace_atomic`: 2 stringhe
    `[REPLACE ATOMIC] cleanup difensivo orfani stg/bak fallito`
    `[REPLACE ATOMIC] cleanup shadow post-INSERT fail fallito`
  - `_truncate_insert_oracle_redef`: 3 stringhe
    `[ORACLE REDEF] cleanup difensivo interim orfana fallito`
    `[ORACLE REDEF] cleanup interim post-START_REDEF fail fallito`
    `[ORACLE REDEF] drop interim post-ABORT fallito`

`inspect.getsource(method)` legge via `__code__.co_filename` + line
numbers → trova il source DA QUESTO FILE post-extraction.

AST callsite contract (test B2.4a)
==================================
3 metodi DEVONO avere ≥1 callsite di `_execute_batch(target_table=...)`:
  - `_replace_atomic` → `target_table=stg_table` (linea ~1166 in
    pre-extraction)
  - `_truncate_insert_stage_swap` → `target_table=stg_table` (~942)
  - `_truncate_insert_oracle_redef` → `target_table=interim` (~1623)

Il test post-B2.4a usa `inspect.getsource(method)` + `ast.parse(class _S:\\n + src)`
→ trova i callsite nel source dei metodi DA QUESTO FILE.

Cross-mixin / parent dependencies
==================================
- `_truncate_insert_atomic`, `_truncate_insert_stage_swap`,
  `_replace_atomic`, `_truncate_insert_oracle_redef` chiamano
  `self._execute_batch(...)` → `TransactionExecutionMixin` (sibling
  mixin, B2.3). MRO risolve.
- `_replace_atomic` chiama `self._table_exists_for(...)` →
  `SchemaIntrospectionMixin` (sibling mixin, B2.2). MRO risolve.
- Tutti i 4 strategy methods chiamano `self._oracle_setup(conn)` →
  `_oracle_setup` resta in `database_writer.py` (NON estratto).
  MRO: name lookup risolve nella classe principale.
- `_truncate_insert_stage_swap` chiama
  `self._mssql_replicate_schema_to_shadow(...)` → intra-cluster.
- `_replace_atomic` chiama `self._generate_single_rename(...)` →
  intra-cluster.
- `self.report_progress(...)` → `DestinationWriter` (base class).
- `self.config.{schema_name, table_name, batch_size}`,
  `self.engine`, `self.db_type`, `self.ddl` → instance attrs
  inizializzati da `DatabaseWriter.__init__`.

Telemetry logger
================
`_truncate_insert_stage_swap` e `_truncate_insert_oracle_redef`
emettono telemetry events (`event=stage_swap.dispatched`) tramite
`_telemetry_logger` (stdlib `logging.getLogger("app.core.carica.writer")`).
Spostando i metodi nel mixin, il `_telemetry_logger` deve essere
accessibile nello scope del mixin → re-creato qui come stdlib
logger omonimo, identico al pattern di `database_writer.py:64`.

Pattern: Mixin class
====================
`ReplaceStrategiesMixin` viene ereditato da `DatabaseWriter`:

    class DatabaseWriter(
        DataPrepMixin,
        SchemaIntrospectionMixin,
        TransactionExecutionMixin,
        ReplaceStrategiesMixin,
        DestinationWriter,
    ):
        ...

I 6 metodi restano accessibili come `DatabaseWriter._<method>` via
MRO. `inspect.getsource(DatabaseWriter._<method>)` legge il source
DA QUESTO FILE.

Coupling test contract — verifica preservazione
================================================
1. **`test_database_writer_log_consistency_contract.py` test #5**
   (`test_cleanup_guards_use_warning_not_debug_on_orphan_leftover`):
   itera i 3 metodi via `inspect.getsource(DatabaseWriter._method)` →
   trova le 6 cleanup guards nel source di questo file. ✓ Pattern
   B2.0 modernizzato ratificato.
2. **`test_database_writer_bulk_integration_contract.py` test #18**
   (`test_stage_swap_callsite_propagates_stg_table_to_bulk`): itera
   i 3 metodi caller via `inspect.getsource(method)` + `ast.parse`
   → trova i callsite di `_execute_batch(target_table=...)` nel
   source di questo file. ✓ Pattern B2.4a modernizzato ratificato.
3. **Telemetry test** (`test_stage_swap_emits_dispatch_telemetry`,
   `test_oracle_redef_emits_dispatch_telemetry`): chiamano
   `writer._truncate_insert_stage_swap(...)` /
   `writer._truncate_insert_oracle_redef(...)` su istanza. MRO
   trasparente. ✓
4. **Patch instance-level** (`patch.object(writer, "_execute_batch", ...)`)
   in 7 file integration: instance-level rebind, MRO trasparente. ✓

Logger naming contract
======================
Logger creato esplicitamente come `get_logger("app.core.carica.writer")`
+ `_telemetry_logger = logging.getLogger("app.core.carica.writer")`
(stdlib, per `extra=` payload structured). Stessi nomi di
`database_writer.py` → singleton per nome → handler/caplog agganciati
nei test catturano emit indistintamente da quale modulo li produce.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`database_writer.py` al momento di B2.4b. Nessuna modifica di logica,
comment, log payload, ordine di operazione, transaction boundaries,
commit/rollback timing, cleanup difensivo, telemetry events,
fallback chain, recovery Oracle, MSSQL schema replication,
DBMS_REDEFINITION pipeline.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

from app.config import settings
from app.core.carica.models import (
    BatchProgress,
    ColumnMapping,
    WriteResult,
    WriteStatus,
)
from app.core.carica.writer_batch_loop import run_batched_inserts
from app.utils.logger import get_logger, get_request_id, get_user_id

# Stesso logger name di `database_writer.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.carica.writer". Il `StructuredLogger` wrapper delega a
# `_logger.log(...)` sul logger stdlib omonimo (singleton per nome).
logger = get_logger("app.core.carica.writer")

# Telemetry emits (event=stage_swap.dispatched, ecc.) vanno via stdlib
# logger con stesso name: i campi `extra` diventano attributi del
# LogRecord, cosa che StructuredLogger (wrapper) non supporta di default.
# Identico al pattern di `database_writer.py:64`.
_telemetry_logger = logging.getLogger("app.core.carica.writer")


class ReplaceStrategiesMixin:
    """Mixin per `DatabaseWriter`: strategie replace/truncate (atomic /
    stage+swap / oracle redef / replace_atomic) + 2 helper privati
    intra-cluster (`_mssql_replicate_schema_to_shadow`,
    `_generate_single_rename`).

    Le transaction boundaries (`engine.begin()` per swap/INSERT batch,
    `engine.connect()` per cleanup/DDL singoli) sono incapsulate qui
    IDENTICHE al pre-extraction.
    """

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

                def _process_batch(batch_df, batch_num, _conn=conn):
                    rows_in_batch = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert"
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

                def _process_batch(batch_df, batch_num, _conn=conn):
                    # IMPORTANT: target_table=stg_table per il bulk path.
                    # insert_sql qui punta alla shadow `<src>__stg`, non
                    # alla tabella target. Senza questo override il bulk
                    # caricherebbe i dati direttamente sul target,
                    # rompendo la semantica stage+swap (che esiste apposta
                    # per non toccare il target finche' lo shadow e' completo).
                    rows_in_batch = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=stg_table,
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
            logger.warning(
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

                def _process_batch(batch_df, batch_num, _conn=conn):
                    # target_table=stg_table: insert_sql punta alla shadow
                    # `__stg`, il bulk path deve scrivere lì, non sul target.
                    rows = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=stg_table,
                    )
                    result.rows_written += rows
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

        _telemetry_logger.info(
            f"Stage+swap dispatch: dialect={self.db_type} path=oracle_redef "
            f"table={schema}.{src} rows={len(df)}",
            extra={
                "event": "stage_swap.dispatched",
                "dialect": self.db_type,
                "path": "oracle_redef",
                "schema": schema,
                "table": src,
                "rows": len(df),
                "threshold": int(settings.CARICA_STAGE_SWAP_THRESHOLD or 0),
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )

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
            logger.warning(
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
                logger.warning(
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

                def _process_batch(batch_df, batch_num, _conn=conn):
                    # Oracle online_redefinition: insert_sql punta alla
                    # interim `__RDF`. Bulk path su Oracle non e'
                    # implementato (dispatcher solleva NotSupported →
                    # fallback executemany), ma passiamo target_table
                    # comunque per safety futura.
                    rows = self._execute_batch(
                        _conn, batch_df, active_cols, insert_sql, "insert",
                        target_table=interim,
                    )
                    result.rows_written += rows
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
                logger.warning(
                    "[ORACLE REDEF] drop interim post-ABORT fallito, ignoro: %s: %s",
                    type(cleanup_err).__name__,
                    cleanup_err,
                )

            result.rows_written = 0
            result.status = WriteStatus.WRITE_ERROR
            result.error_message = f"DBMS_REDEFINITION fallimento: {e}"
            result.error_details = repr(e)
            return result
