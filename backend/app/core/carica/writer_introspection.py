"""
Mixin SCHEMA_INTROSPECTION — schema introspection (DB-side + DataFrame-side)
per DatabaseWriter.

Origine
=======
Estratto da `app.core.carica.database_writer.DatabaseWriter` come parte
di Phase 5 Batch B2.2 (cluster split + mixin pattern, vedi
`orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B2 + §6.1 facade pattern
ratificato in B1; mixin pattern ratificato in B2.1).

Razionale del raggruppamento: 6 metodi che condividono il dominio
"schema introspection / DDL planning input" — read-only operations
sia sul DB target sia sul DataFrame input, **zero transaction
semantics**, zero DDL execution:

  - `table_exists`            — read-only DB check sulla tabella di
                                 destinazione (`self.config.schema_name`,
                                 `self.config.table_name`).
  - `get_existing_columns`    — read-only DB scan colonne della tabella
                                 di destinazione.
  - `get_schemas`             — read-only DB lista schema visibili.
  - `get_tables`              — read-only DB lista tabelle in uno schema.
  - `_table_exists_for`        — variante di `table_exists` per coppia
                                 (schema, table) arbitraria; usato da
                                 `_replace_atomic` per scegliere
                                 swap-vs-rename.
  - `_compute_schema_hints`   — pure computation: ispeziona dtype del
                                 DataFrame + max-length stringhe per
                                 produrre `(real_dtypes, max_lengths)`
                                 hints per `DDLGenerator.generate_create_table`.
                                 Zero DB I/O. Coeso col cluster perche'
                                 e' "schema introspection lato DataFrame".

Tutti i 5 metodi DB-side seguono lo stesso pattern fail-fast:
`try / engine.connect() / except: logger.error([CRASH]) + raise` —
mai silent return su errore introspection (un check fallito non
deve essere confuso con "tabella non trovata").

Pattern: Mixin class
====================
`SchemaIntrospectionMixin` viene ereditato da `DatabaseWriter`:

    class DatabaseWriter(DataPrepMixin, SchemaIntrospectionMixin,
                         DestinationWriter):
        ...

I 6 metodi restano accessibili come `DatabaseWriter._<method>` /
`DatabaseWriter.<method>` via MRO. `inspect.getsource(DatabaseWriter
.<method>)` legge il source DA QUESTO FILE. Le call-site
`self.<method>(...)` nei metodi che restano in `database_writer.py`
(`validate`, `_dispatch_write_mode`, `_replace_atomic`) continuano a
risolvere via MRO senza modifiche.

Coupling esterno
================
- `self.engine` — usato dai 5 metodi DB-side (`engine.connect()`).
- `self.config.schema_name`, `self.config.table_name` — usati da
  `table_exists` e `get_existing_columns` per il target di default.
- `self.ddl` — usato dai 5 metodi DB-side per `query_table_exists` /
  `query_table_columns` / `query_list_schemas` / `query_list_tables`
  (`DDLGenerator` instance, dialect-aware).

Nessuna dipendenza su `self.db_type`, `self.progress_callback`,
`self.connection`. Cluster zero-transaction, zero DDL execution
(solo SELECT/SHOW). Sicuro per estrazione anche con vincoli
transazionali del modulo parent.

Coupling test contract
======================
- `test_database_writer_introspection_failfast_contract.py` testa i
  4 metodi DB-side (`table_exists`, `get_existing_columns`,
  `get_schemas`, `get_tables`) verificando il pattern fail-fast
  (raise su errore query). Usa instance-level
  `writer.<method>()`. MRO trasparente: chiamate continuano a
  risolvere al metodo del mixin via inheritance. ✓
- `test_database_writer_preflight_failclosed_contract.py:147` testa
  `_table_exists_for` (raise on error invece del legacy silent return
  False). Instance-level. MRO trasparente. ✓
- `test_database_writer_log_consistency_contract.py` (B2.0
  modernized) NON tocca questi 6 metodi. Resta verde.

Logger naming contract
======================
Logger creato esplicitamente come `get_logger("app.core.carica.writer")`
(NON `__name__`) per preservare il contratto osservabile pre-extraction.
Il `StructuredLogger` wrapper di `app/utils/logger.py` delega a
`_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
quindi gli emit dal mixin (qui) finiscono sullo stesso logger
underlying di `database_writer.py` — handler agganciati nei test
B2.0 catturano gli emit indistintamente.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`database_writer.py` al momento di B2.2. Nessuna modifica di logica,
comment, log payload, ordine di operazione, fail-fast pattern,
type-guard scalar conversion, mass-coerce di string-like dtype
detection.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

from app.core.carica.models import ColumnMapping
from app.utils.logger import get_logger

# Stesso logger name di `database_writer.py`: i record sono
# indistinguibili dai consumer che attaccano handler / filtrano per
# "app.core.carica.writer". Il `StructuredLogger` wrapper delega a
# `_logger.log(...)` sul logger stdlib omonimo (singleton per nome),
# quindi handler agganciati nei test B2.0 catturano gli emit dal mixin.
logger = get_logger("app.core.carica.writer")


class SchemaIntrospectionMixin:
    """Mixin per `DatabaseWriter`: schema introspection read-only su
    DB target + ispezione schema lato DataFrame per DDL planning.
    Zero transaction semantics, zero DDL execution.
    """

    # ========================================================================
    # INTROSPECTION DB-SIDE
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

    def _table_exists_for(self, schema: str, table: str) -> bool:
        """table_exists per una coppia (schema, table) arbitraria
        (a differenza di self.table_exists che usa self.config).

        Solleva l'eccezione sottostante se la query non e' eseguibile.
        Pre-fix: logger.warning + return False — ma il valore e' usato
        da _replace_atomic per scegliere swap vs single-rename, quindi
        un errore query silenziato produceva routing errato e fallimento
        confuso downstream. Allineato al pattern di self.table_exists.
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

    # ========================================================================
    # INTROSPECTION DATAFRAME-SIDE (DDL planning)
    # ========================================================================

    def _compute_schema_hints(
        self,
        prepared_df: pd.DataFrame,
        columns: List[ColumnMapping],
    ) -> Tuple[Dict[str, str], Dict[str, int]]:
        """Calcola `(real_dtypes, max_lengths)` per `generate_create_table`.

        Estratto da `write()` (C4.2 Phase 4). Pre-extraction la stessa
        logica era duplicata bit-per-bit nei branch CREATE puro e
        CREATE-via-REPLACE atomic. Helper privato, pura computazione,
        nessun side-effect, nessuna chiamata DB.

        - `real_dtypes`: mapping colonna → dtype string. Include sia i
          nomi colonna del DataFrame sia i `source_column`/`dest_column`
          dei `ColumnMapping` (override esplicito quando entrambi presenti
          nel DataFrame). Serve a `DDLGenerator` per type mapping
          deterministico, evitando inferenze sbagliate quando la sorgente
          ha alias diversi dalla destinazione.

        - `max_lengths`: mapping colonna string-like → lunghezza massima
          osservata (one-shot pass). Su Oracle abilita la promozione
          automatica VARCHAR2(4000) → CLOB per colonne lunghe (C5 fix 2);
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
