"""
Pre-check conflitti per il modulo CARICA.

Keeps database loading behavior isolated from orchestration callers.
"""

from app.utils.logger import get_logger
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.models import ConflictDetail, ConflictReport


logger = get_logger("app.core.carica.conflict")


class ConflictChecker:
    """Verifica conflitti pre-inserimento.

    Attributes:
        engine: SQLAlchemy engine per la connessione al DB destinazione
        db_type: tipo database ("postgres", "oracle", "mssql", "mysql")
        ddl: `DDLGenerator` boundary per quoting/qualified-name SQL.
             il caller di solito
            inietta `self.ddl` del writer (gia' istanziato in
            `DatabaseWriter.__init__`) per evitare doppia istanza +
            duplicazione delle policy di quoting/qualified-name.
            Se `None`, viene costruito un `DDLGenerator(db_type)` ad
            hoc — backward-compat per callsite esistenti
            (`router.py`, test legacy che passano solo engine+db_type).
    """

    # Numero massimo di valori nella clausola IN per query
    MAX_IN_BATCH = 500
    # Numero massimo di dettagli conflitto da restituire
    MAX_DETAILS = 100

    def __init__(
        self,
        engine: Engine,
        db_type: str,
        *,
        ddl: Optional[DDLGenerator] = None,
    ):
        self.engine = engine
        self.db_type = db_type.lower()
        # consume `DDLGenerator` come boundary SQL/dialetto. La fallback
        # locale al ddl=None preserva backward-compat per callsite
        # esistenti che non passano `ddl=...`
        self.ddl = ddl if ddl is not None else DDLGenerator(self.db_type)

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

        internal_dups = self._check_internal_duplicates(df, match_columns)
        report.internal_duplicates = internal_dups

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

        Usa query IN(...) in verificare l'esistenza dei valori.
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
        """Check conflitti su più colonne match — versione batch.

        Strategia: invece di N query (una per combinazione), carica tutte le
        combinazioni esistenti nella tabella destinazione con una SINGOLA SELECT
        DISTINCT e fa il match in memoria. Drastico miglioramento performance
        su dataset con molte combinazioni uniche (era N+1).
        """
        full_table = self._qualified_name(schema, table)

        # Estrai combinazioni uniche nel DataFrame sorgente
        unique_combos = df[match_columns].drop_duplicates()
        if unique_combos.empty:
            return 0, []

        # UNA sola query: carica tutte le combinazioni distinct della destinazione
        quoted_cols = ", ".join(self._quote_col(c) for c in match_columns)
        query = f"SELECT DISTINCT {quoted_cols} FROM {full_table}"

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))
                existing_rows = result.fetchall()
        except Exception as e:
            logger.error(f"[CRASH] Errore query multi-column destinazione: {e}", exc_info=True)
            return 0, []

        # Converte in set di tuple per lookup O(1
        # Sentinel per NaN (None non è hashable in tuple con NaN reali
        _NAN_SENTINEL = "__NAN__"

        def _normalize(v):
            if pd.isna(v):
                return _NAN_SENTINEL
            return _serialize_value(v)

        existing_set = set()
        for row in existing_rows:
            values = tuple(_normalize(v) for v in row)
            existing_set.add(values)

        # Match in memoria: O(n) invece di N query DB
        conflict_count = 0
        details = []

        for _, combo_row in unique_combos.iterrows():
            combo_key = tuple(_normalize(combo_row[col]) for col in match_columns)
            if combo_key in existing_set:
                conflict_count += 1
                if len(details) < self.MAX_DETAILS:
                    match_vals = {
                        col: _serialize_value(combo_row[col])
                        for col in match_columns
                    }
                    # Trova righe nel DataFrame con questa combo
                    mask = pd.Series(True, index=df.index)
                    for col in match_columns:
                        if pd.isna(combo_row[col]):
                            mask &= df[col].isna()
                        else:
                            mask &= (df[col] == combo_row[col])
                    matching_indices = df[mask].index.tolist()
                    for row_idx in matching_indices[:5]:
                        if len(details) >= self.MAX_DETAILS:
                            break
                        details.append(ConflictDetail(
                            row_index=int(row_idx),
                            match_values=match_vals,
                            conflict_type="pk_duplicate",
                        ))

            if conflict_count > 1000:
                logger.warning("Superati 1000 conflitti, interrompo check")
                break

        return conflict_count, details[:self.MAX_DETAILS]


    def _qualified_name(self, schema: str, table: str) -> str:
        """Nome tabella qualificato con schema (delega a DDLGenerator)."""
        return self.ddl.qualified_table_name(schema, table)

    def _quote_col(self, name: str) -> str:
        """Quota un nome colonna (delega a DDLGenerator)."""
        return self.ddl.quote_column(name)


def _serialize_value(val) -> Any:
    """Converte un valore per la serializzazione JSON."""
    if pd.isna(val):
        return None
    if hasattr(val, 'item'):  # numpy scalar
        return val.item()
    return val
