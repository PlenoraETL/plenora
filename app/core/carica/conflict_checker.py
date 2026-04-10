"""
Pre-check conflitti per il modulo CARICA.

Architettura
============
Questo modulo verifica i conflitti prima dell'inserimento in modalità APPEND.
Il check è in due passaggi:

Passaggio 1 — Duplicati interni:
    Verifica se il DataFrame sorgente contiene righe duplicate sulle
    colonne match (PK/UNIQUE). Segnala quante righe sono duplicate.

Passaggio 2 — Conflitti con DB:
    Interroga la tabella destinazione per verificare se i valori delle
    colonne match esistono già. Usa query IN(...) in batch per evitare
    query troppo grandi.

Il risultato è un ConflictReport che il frontend mostra all'utente
con le opzioni: "Procedi (skip conflitti)", "Forza (sovrascrivi)",
"Annulla". In modalità CRON, la ConflictPolicy preconfigurata decide
automaticamente.

Uso:
    checker = ConflictChecker(engine, db_type)
    report = checker.check(df, schema, table, match_columns)
"""

from app.utils.logger import get_logger
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.carica.models import ConflictDetail, ConflictReport


logger = get_logger("app.core.carica.conflict")


class ConflictChecker:
    """Verifica conflitti pre-inserimento.

    Attributes:
        engine: SQLAlchemy engine per la connessione al DB destinazione
        db_type: tipo database ("postgres", "oracle", "mssql", "mysql")
    """

    # Numero massimo di valori nella clausola IN per query
    MAX_IN_BATCH = 500
    # Numero massimo di dettagli conflitto da restituire
    MAX_DETAILS = 100

    def __init__(self, engine: Engine, db_type: str):
        self.engine = engine
        self.db_type = db_type.lower()

    def check(
        self,
        df: pd.DataFrame,
        schema: str,
        table: str,
        match_columns: List[str],
    ) -> ConflictReport:
        """Esegue il check completo dei conflitti.

        Args:
            df: DataFrame con i dati da inserire
            schema: schema della tabella destinazione
            table: nome tabella destinazione
            match_columns: colonne su cui verificare i conflitti

        Returns:
            ConflictReport con tutti i conflitti trovati.
        """
        report = ConflictReport(checked_at=datetime.now(timezone.utc).isoformat())

        if not match_columns:
            logger.info("Nessuna colonna match specificata, skip check conflitti")
            return report

        # Verifica che le colonne match esistano nel DataFrame
        missing = [c for c in match_columns if c not in df.columns]
        if missing:
            logger.warning(f"Colonne match mancanti nel DataFrame: {missing}")
            return report

        # === Passaggio 1: Duplicati interni ===
        internal_dups = self._check_internal_duplicates(df, match_columns)
        report.internal_duplicates = internal_dups

        # === Passaggio 2: Conflitti con DB ===
        db_conflicts, details = self._check_db_conflicts(
            df, schema, table, match_columns
        )
        report.db_conflicts = db_conflicts
        report.details = details

        # Riepilogo
        report.total_conflicts = internal_dups + db_conflicts
        report.has_conflicts = report.total_conflicts > 0

        if report.has_conflicts:
            logger.info(
                f"Conflitti trovati: {internal_dups} duplicati interni, "
                f"{db_conflicts} conflitti con DB"
            )

        return report

    def _check_internal_duplicates(
        self,
        df: pd.DataFrame,
        match_columns: List[str],
    ) -> int:
        """Passaggio 1: cerca duplicati interni al DataFrame.

        Returns:
            Numero di righe duplicate (conteggio righe in eccesso,
            non il totale dei gruppi).
        """
        try:
            duplicated = df.duplicated(subset=match_columns, keep='first')
            dup_count = int(duplicated.sum())
            if dup_count > 0:
                logger.info(
                    f"Trovati {dup_count} duplicati interni su colonne {match_columns}"
                )
            return dup_count
        except Exception as e:
            logger.error(f"[CRASH] Errore check duplicati interni: {e}", exc_info=True)
            return 0

    def _check_db_conflicts(
        self,
        df: pd.DataFrame,
        schema: str,
        table: str,
        match_columns: List[str],
    ) -> tuple:
        """Passaggio 2: cerca conflitti con record già nel DB.

        Usa query IN(...) in batch per verificare l'esistenza dei valori.
        Per match su singola colonna: SELECT col FROM tab WHERE col IN (...)
        Per match multi-colonna: usa confronto tupla o query EXISTS

        Returns:
            Tupla (count_conflitti, lista_dettagli).
        """
        try:
            if len(match_columns) == 1:
                return self._check_single_column(df, schema, table, match_columns[0])
            else:
                return self._check_multi_column(df, schema, table, match_columns)
        except Exception as e:
            logger.error(f"[CRASH] Errore check conflitti DB: {e}", exc_info=True)
            return 0, []

    def _check_single_column(
        self,
        df: pd.DataFrame,
        schema: str,
        table: str,
        column: str,
    ) -> tuple:
        """Check conflitti su singola colonna match."""
        # Valori unici dalla sorgente
        values = df[column].dropna().unique().tolist()
        if not values:
            return 0, []

        full_table = self._qualified_name(schema, table)
        quoted_col = self._quote_col(column)

        conflicting_values = set()

        # Query in batch
        for i in range(0, len(values), self.MAX_IN_BATCH):
            batch = values[i:i + self.MAX_IN_BATCH]
            placeholders = ", ".join(f":v_{j}" for j in range(len(batch)))
            params = {f"v_{j}": _serialize_value(v) for j, v in enumerate(batch)}

            query = f"SELECT {quoted_col} FROM {full_table} WHERE {quoted_col} IN ({placeholders})"

            try:
                with self.engine.connect() as conn:
                    result = conn.execute(text(query), params)
                    for row in result:
                        conflicting_values.add(row[0])
            except Exception as e:
                logger.error(f"[CRASH] Errore query conflitti batch: {e}", exc_info=True)
                continue

        # Costruisci dettagli
        details = []
        if conflicting_values:
            conflict_mask = df[column].isin(conflicting_values)
            conflict_indices = df[conflict_mask].index.tolist()

            for idx in conflict_indices[:self.MAX_DETAILS]:
                row = df.loc[idx]
                details.append(ConflictDetail(
                    row_index=int(idx),
                    match_values={column: _serialize_value(row[column])},
                    conflict_type="pk_duplicate",
                ))

        return len(conflicting_values), details

    def _check_multi_column(
        self,
        df: pd.DataFrame,
        schema: str,
        table: str,
        match_columns: List[str],
    ) -> tuple:
        """Check conflitti su più colonne match.

        Usa query con AND per ogni combinazione di valori.
        Più lento ma compatibile con tutti i database.
        """
        full_table = self._qualified_name(schema, table)

        # Estrai combinazioni uniche
        unique_combos = df[match_columns].drop_duplicates()
        conflict_count = 0
        details = []

        for idx, combo_row in unique_combos.iterrows():
            # Costruisci WHERE clause
            where_parts = []
            params = {}
            for col in match_columns:
                val = combo_row[col]
                if pd.isna(val):
                    where_parts.append(f"{self._quote_col(col)} IS NULL")
                else:
                    param_name = f"m_{col}"
                    where_parts.append(f"{self._quote_col(col)} = :{param_name}")
                    params[param_name] = _serialize_value(val)

            where_sql = " AND ".join(where_parts)
            query = f"SELECT COUNT(*) FROM {full_table} WHERE {where_sql}"

            try:
                with self.engine.connect() as conn:
                    result = conn.execute(text(query), params)
                    count = result.scalar()

                    if count and count > 0:
                        conflict_count += 1
                        if len(details) < self.MAX_DETAILS:
                            match_vals = {
                                col: _serialize_value(combo_row[col])
                                for col in match_columns
                            }
                            # Trova le righe nel DataFrame con questa combo
                            mask = pd.Series(True, index=df.index)
                            for col in match_columns:
                                if pd.isna(combo_row[col]):
                                    mask &= df[col].isna()
                                else:
                                    mask &= (df[col] == combo_row[col])
                            matching_indices = df[mask].index.tolist()
                            for row_idx in matching_indices[:5]:
                                details.append(ConflictDetail(
                                    row_index=int(row_idx),
                                    match_values=match_vals,
                                    conflict_type="pk_duplicate",
                                ))
            except Exception as e:
                logger.error(f"[CRASH] Errore query conflitto multi-colonna: {e}", exc_info=True)
                continue

            # Safety: evita di fare troppe query
            if conflict_count > 1000:
                logger.warning("Superati 1000 conflitti, interrompo check")
                break

        return conflict_count, details[:self.MAX_DETAILS]

    # ========================================================================
    # HELPER
    # ========================================================================

    def _qualified_name(self, schema: str, table: str) -> str:
        """Nome tabella qualificato con schema."""
        if self.db_type in ("mysql", "mariadb"):
            return f"`{schema}`.`{table}`" if schema else f"`{table}`"
        elif self.db_type == "oracle":
            s = schema.upper() if schema else ""
            t = table.upper()
            return f'"{s}"."{t}"' if s else f'"{t}"'
        else:
            return f'"{schema}"."{table}"' if schema else f'"{table}"'

    def _quote_col(self, name: str) -> str:
        """Quota un nome colonna."""
        if self.db_type in ("mysql", "mariadb"):
            return f"`{name}`"
        elif self.db_type == "oracle":
            return f'"{name.upper()}"'
        return f'"{name}"'


def _serialize_value(val) -> Any:
    """Converte un valore per la serializzazione JSON."""
    if pd.isna(val):
        return None
    if hasattr(val, 'item'):  # numpy scalar
        return val.item()
    return val
