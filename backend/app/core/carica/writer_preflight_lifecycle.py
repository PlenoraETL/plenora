"""
Mixin PREFLIGHT_LIFECYCLE — preflight validation + DDL lifecycle on

target table per DatabaseWriter.
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
    WriteMode,
    WriteResult,
)
from app.utils.logger import get_logger

# Keep the canonical writer logger name so existing handlers receive mixin logs.
logger = get_logger("app.core.carica.writer")


class WriterPreflightLifecycleMixin:
    """Mixin per `DatabaseWriter`: preflight validation + DDL lifecycle
    on target table. Pure pre-batch-write preparation. Zero
    transaction-orchestration cross-method (ogni metodo apre e chiude
    la propria connection).
    """

    def validate(self) -> Tuple[bool, str]:
        """Valida le pre-condizioni prima della scrittura."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            return False, f"Connessione al database fallita: {str(e)}"

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

        if self.config.write_mode in (WriteMode.UPDATE, WriteMode.UPSERT):
            if not self.config.match_columns:
                return False, (
                    f"La modalità {self.config.write_mode.value} richiede "
                    f"almeno una colonna match (chiave primaria)."
                )

        if self.config.write_mode in (WriteMode.CREATE, WriteMode.REPLACE):
            ok, err = self._check_create_permission()
            if not ok:
                return False, err

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
            # Unknown CREATE probe errors are fail-closed so callers do not
            # continue with misleading permission assumptions.
            logger.error(
                f"[CRASH] Verifica permessi CREATE fallita su schema "
                f"'{self.config.schema_name}': {e}",
                exc_info=True,
            )
            return False, (
                f"Verifica permessi CREATE TABLE nello schema "
                f"'{self.config.schema_name}' fallita: {str(e)}"
            )

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
            max_lengths: {col_name: max_char_length} opzionale.
                Se passato, su Oracle abilita auto-promote VARCHAR2(4000)
                → CLOB per colonne > 4000 char.
        """
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
        (permessi, lock, DDL), cosi' il boundary in write segnala
        WRITE_ERROR invece di proseguire con un insert non preceduto da truncate.
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

    def check_conflicts(
        self,
        df: pd.DataFrame,
        match_columns: List[str],
    ) -> ConflictReport:
        """Pre-check conflitti per APPEND.

         passa esplicitamente
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

    def _build_initial_result(
        self, total_rows: int, started_at: str
    ) -> WriteResult:
        """Costruisce lo skeleton del WriteResult prima dell'esecuzione.

        Pura factory: nessuna logica di business, nessun side-effect.
        Mantiene un'unica derivazione dei campi table/write mode.
        """
        return WriteResult(
            started_at=started_at,
            total_rows=total_rows,
            table_name=f"{self.config.schema_name}.{self.config.table_name}",
            write_mode=self.config.write_mode.value,
        )

    def _assert_safe_write_config(self) -> None:
        """Gate fail-loud sulla combo append/per_batch senza match columns.

        Se il write fallisce a meta' con ' committati (PER_BATCH)
        + write_mode=APPEND + nessun match_columns per conflict detection,
        un rilancio manuale duplicherebbe le righe gia' scritte.
        La combo e' rifiutata fail-loud, opt-in tramite
        `WriteConfig.allow_unsafe_append=True` per chi accetta
        consapevolmente il rischio (es. tabelle append-only con
        lineage esterno che gestisce dedup downstream).
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
        # Opt-in: warning informativo per l'operatore, write procede
        logger.warning(
            f"[UNSAFE WRITE CONFIG opt-in] Tabella "
            f"{self.config.schema_name}.{self.config.table_name}: "
            f"write_mode=append + transaction_mode=per_batch + "
            f"nessun match_columns + allow_unsafe_append=True. "
            f"Retry parziali possono duplicare righe."
        )
