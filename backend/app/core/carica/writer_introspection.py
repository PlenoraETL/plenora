"""
Mixin SCHEMA_INTROSPECTION — schema introspection (DB-side + DataFrame-side)

per DatabaseWriter.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from app.core.carica.models import ColumnMapping
from app.shared.contracts.payloads import IntMap, StringMap, StringRows
from app.utils.logger import get_logger

# Keep the canonical writer logger name so existing handlers receive mixin logs.
logger = get_logger("app.core.carica.writer")


class SchemaIntrospectionMixin:
    """Mixin per `DatabaseWriter`: schema introspection read-only su
    DB target + ispezione schema lato DataFrame per DDL planning.
    Zero transaction semantics, zero DDL execution.
    """

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
                # bool. Tentativo di conversione protetta da try/except
                try:
                    return int(count) > 0
                except (ValueError, TypeError):
                    # Fallback: truthy check (es. "true", "yes", non-empty
                    return bool(count)
        except Exception as e:
            logger.error(f"[CRASH] Errore verifica esistenza tabella: {e}", exc_info=True)
            raise

    def get_existing_columns(self) -> StringRows:
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

    def _table_exists_for(self, schema: str, table: str) -> bool:
        """table_exists per una coppia (schema, table) arbitraria
        (a differenza di self.table_exists che usa self.config).

        Solleva l'eccezione sottostante se la query non e' eseguibile.
        Errori di query propagano per evitare routing errato nello swap.
        """
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
            logger.error(
                f"[CRASH] Verifica esistenza tabella {schema}.{table} fallita: {e}",
                exc_info=True,
            )
            raise

    def _compute_schema_hints(
        self,
        prepared_df: Any,
        columns: List[ColumnMapping],
    ) -> Tuple[StringMap, IntMap]:
        """Calcola `(real_dtypes, max_lengths)` per `generate_create_table`.

        Helper privato, pura computazione, nessun side-effect, nessuna
        chiamata DB.

        - `real_dtypes`: mapping colonna → dtype string. Include sia i
          nomi colonna del DataFrame sia i `source_column`/`dest_column`
          dei `ColumnMapping` (override esplicito quando entrambi presenti
          nel DataFrame). Serve a `DDLGenerator` per type mapping
          deterministico, evitando inferenze sbagliate quando la sorgente
          ha alias diversi dalla destinazione.

        - `max_lengths`: mapping colonna string-like → lunghezza massima
          osservata (one-shot pass). Su Oracle abilita la promozione
          automatica VARCHAR2(4000) → CLOB per colonne lunghe;
          su altri dialetti l'info viene ignorata da `generate_create_table`,
          ma il calcolo resta cheap su DataFrame gia' in RAM.

        Dtype check difensivo: `_fix_mixed_type_columns` puo' aver
        convertito colonne a dtype='str' (non 'object'). La whitelist
        copre `object`/`string`/`category`/`str` + qualunque dtype che
        inizi con `string` (pandas StringDtype moderno).
        """
        real_dtypes: Dict[str, str] = {
            col: str(prepared_df[col].dtype) for col in prepared_df.columns
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

        max_lengths: Dict[str, int] = {}
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
                        prepared_df[col_name].astype(str).str.len().max() or 0
                    )
                except Exception as max_len_err:
                    logger.debug(
                        "[MAX_LEN] calcolo lunghezza colonna '%s' "
                        "fallito, assumo 0: %s: %s",
                        col_name,
                        type(max_len_err).__name__,
                        max_len_err,
                    )
                    max_len = 0
                if max_len > 0:
                    max_lengths[col_name] = max_len

        return real_dtypes, max_lengths
