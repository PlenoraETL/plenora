"""
Mixin BATCH_DISPATCH — dispatch per write mode (insert/update/upsert)

verso il di TransactionExecutionMixin.
"""

from __future__ import annotations

from typing import List

import pandas as pd
from sqlalchemy import text

from app.core.carica.models import ColumnMapping, WriteResult
from app.utils.logger import get_logger

# Stesso logger name di `database_writer.py` per handler e filtri condivisi.
logger = get_logger("app.core.carica.writer")


class BatchDispatchMixin:
    """Mixin per `DatabaseWriter`: build SQL template per write mode +
    delegate a `_execute_in_batches`. Livello d'astrazione tra
    `_dispatch_write_mode` (decide write mode) e `_execute_in_batches`
    (gestisce loop + transaction strategies).
    """


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


    def _batch_update(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Aggiorna righe in batch."""
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


    def _batch_upsert(
        self,
        df: pd.DataFrame,
        columns: List[ColumnMapping],
        result: WriteResult,
    ) -> WriteResult:
        """Esegue UPSERT in batch."""
        # PostgreSQL UPSERT richiede un vincolo UNIQUE/PK sulle match_columns.
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
