"""
Mixin PREFLIGHT_LIFECYCLE — preflight validation + DDL lifecycle on
target table per DatabaseWriter.

Origine
=======
Estratto da `app.core.carica.database_writer.DatabaseWriter` come parte
di Phase 5 Batch B2.5b (cluster split + mixin pattern, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2; mixin pattern ratificato
in B2.1+B2.2+B2.3+B2.4b).

Pre-extraction — B2.5a sblocca il test contract
================================================
Pre-B2.5a `test_unsafe_append_combo_with_flag_proceeds` patchava
`monkeypatch.setattr(dw_mod, "logger", spy_logger)` per verificare
il warning emesso da `_assert_safe_write_config`. Patch source-location
bound: spostando il metodo in mixin, il `logger` letto dal mixin (un
altro StructuredLogger wrapper) NON sarebbe stato visto dal mock →
test rosso anche se il warning viene emesso correttamente.

B2.5a ha modernizzato il test usando handler stdlib agganciato al
logger canonico per nome (stesso pattern di B2.0
`test_database_writer_log_consistency_contract.py`). Post-B2.5a
l'extraction qui non rompe alcun contratto.

Razionale del raggruppamento: 8 metodi che coprono il dominio
"pre-batch-write preparation": verifiche di pre-condizione
(`validate` + `_check_create_permission`), gate fail-loud per write
config rischiose (`_assert_safe_write_config`), factory `WriteResult`
(`_build_initial_result`), conflict checking pre-APPEND
(`check_conflicts`), e DDL CRUD sul target (`create_table` /
`drop_table` / `truncate_table`). Cluster naturalmente coeso nel
flusso `write()`:

  1. `write()` chiama `_assert_safe_write_config()` (gate fail-loud)
     e `_build_initial_result()` (factory result skeleton).
  2. `validate()` chiama `_check_create_permission()` intra-cluster.
  3. `_dispatch_write_mode()` chiama `create_table()`, `truncate_table()`,
     `check_conflicts()` come fasi del dispatch per write mode.
  4. `drop_table()` resta esposto per uso esterno (CARICA admin/cleanup
     tools); attualmente nessun caller interno post-B2.4b.

Cluster scope
=============
**Preflight (5 metodi)**:
  - `validate`                   — verifica pre-condizioni: connessione,
                                   esistenza tabella per modalita',
                                   match_columns per UPDATE/UPSERT,
                                   permessi CREATE per CREATE/REPLACE,
                                   colonne destinazione valide.
  - `_check_create_permission`   — fail-closed CREATE permission check
                                   tramite create+drop di tabella temporanea.
  - `_assert_safe_write_config`  — gate fail-loud F-DATA-08 / P1-05
                                   sulla combo APPEND + PER_BATCH +
                                   no match_columns (corruzione su
                                   retry partial). Warning informativo
                                   in path opt-in (catturato da test
                                   B2.5a-modernizzato via handler
                                   stdlib agganciato al nome canonico).
  - `_build_initial_result`      — factory `WriteResult` skeleton da
                                   `self.config`.
  - `check_conflicts`            — pre-check conflitti APPEND via
                                   `ConflictChecker`.

**DDL lifecycle (3 metodi)**:
  - `create_table`               — CREATE TABLE + spatial index +
                                   unique constraint (per UPSERT).
  - `drop_table`                 — DROP TABLE (admin/cleanup, no
                                   caller interno post-B2.4b).
  - `truncate_table`             — TRUNCATE TABLE fail-fast (raise
                                   on error invece di return False
                                   silenzioso).

Coupling esterno
================
- `self.config.{schema_name, table_name, write_mode, if_table_exists,
  match_columns, column_mappings, transaction_mode, allow_unsafe_append,
  create_spatial_index, geometry_srid, unique_key_column}` —
  instance attrs inizializzati da `DatabaseWriter.__init__`.
- `self.engine` — SQLAlchemy engine.
- `self.db_type` — dialect string.
- `self.ddl` — `DDLGenerator` instance, dialect-aware.
- `self.table_exists()`, `self.get_existing_columns()` —
  `SchemaIntrospectionMixin` (sibling, B2.2). MRO risolve.
- `ConflictChecker` (top-level import) — `app.core.carica.conflict_checker`.

Cluster zero-transaction-orchestration: tutti i metodi aprono e
chiudono la propria `engine.connect()` con commit manuale dove serve.
NON aprono context spans cross-method. Identico bit-per-bit al
pre-extraction.

Pattern: Mixin class
====================
`WriterPreflightLifecycleMixin` viene ereditato da `DatabaseWriter`:

    class DatabaseWriter(
        DataPrepMixin,
        SchemaIntrospectionMixin,
        TransactionExecutionMixin,
        ReplaceStrategiesMixin,
        WriterPreflightLifecycleMixin,
        DestinationWriter,
    ):
        ...

I 8 metodi restano accessibili come `DatabaseWriter.<method>` /
`DatabaseWriter._<method>` via MRO. `inspect.getsource(DatabaseWriter
.<method>)` legge il source DA QUESTO FILE. Le call-site
`self.<method>(...)` sia interne (intra-cluster) sia dai cluster
esterni (`_dispatch_write_mode → self.create_table` etc.) usano name
lookup che risolve via MRO senza modifiche.

Coupling test contract
======================
1. **Patch instance-level via MRO trasparente** (verificato pre-extraction):
   - Test contract `test_database_writer_introspection_failfast_contract`,
     `test_database_writer_preflight_failclosed_contract`,
     `test_unsafe_append_gate_contract` (post-B2.5a) chiamano i metodi su
     istanza (`writer.validate()`, `writer._check_create_permission()`,
     `writer.truncate_table()`, `writer.write()` che dispatcha
     `_assert_safe_write_config`). MRO trasparente. ✓
2. **Warning capture handler-based (post-B2.5a)**: il warning emesso da
   `_assert_safe_write_config` viene catturato da handler stdlib
   agganciato al logger canonico `"app.core.carica.writer"` per nome.
   Lo `StructuredLogger` wrapper di `app/utils/logger.py` delega a
   `_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
   quindi gli emit dal mixin (qui) finiscono sullo stesso logger
   underlying e vengono catturati indistintamente. ✓
3. **Nessun `inspect.getsource(dw_module)` o AST scan** sui 8 metodi
   target (verificato grep). ✓

Logger naming contract
======================
Logger creato esplicitamente come `get_logger("app.core.carica.writer")`
(NON `__name__`) per preservare il contratto osservabile pre-extraction.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`database_writer.py` al momento di B2.5b. Nessuna modifica di logica,
comment, log payload, ordine di operazione, fail-fast pattern,
permission classification heuristic, gate fail-loud F-DATA-08,
spatial/unique constraint creation, type-guard scalar conversion.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text

from app.core.carica.conflict_checker import ConflictChecker
from app.core.carica.models import (
    ColumnMapping,
    ConflictReport,
    TransactionMode,
    WriteConfig,  # noqa: F401  (used in type docs)
    WriteMode,
    WriteResult,
)
from app.utils.logger import get_logger

# Stesso logger name di `database_writer.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.carica.writer". Il `StructuredLogger` wrapper delega a
# `_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
# quindi handler agganciati nei test (B2.0/B2.5a) catturano gli emit
# dal mixin.
logger = get_logger("app.core.carica.writer")


class WriterPreflightLifecycleMixin:
    """Mixin per `DatabaseWriter`: preflight validation + DDL lifecycle
    on target table. Pure pre-batch-write preparation. Zero
    transaction-orchestration cross-method (ogni metodo apre e chiude
    la propria connection).
    """

    # PREFLIGHT VALIDATION

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

    # GESTIONE TABELLA (DDL lifecycle)

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

    # CHECK CONFLITTI

    def check_conflicts(
        self,
        df: pd.DataFrame,
        match_columns: List[str],
    ) -> ConflictReport:
        """Pre-check conflitti per APPEND.

        BE-GO-READY-TRANSFORMATION-ENGINE-013: passa esplicitamente
        `self.ddl` al ConflictChecker per riutilizzare la boundary
        SQL/dialetto del writer (closure di TE11-R4: niente piu'
        duplicazione di `_qualified_name`/`_quote_col` lato
        `ConflictChecker`).
        """
        checker = ConflictChecker(self.engine, self.db_type, ddl=self.ddl)
        return checker.check(
            df,
            self.config.schema_name,
            self.config.table_name,
            match_columns,
        )

    # WRITE RESULT FACTORY + SAFE-CONFIG GATE

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
