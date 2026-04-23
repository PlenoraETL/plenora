"""
bulk_writers.py — Bulk INSERT nativo per Postgres (COPY).

Sostituisce `conn.execute(text(sql), params_list)` (executemany SQLAlchemy)
sul dialect Postgres dove esiste un protocollo bulk piu' efficiente:

  - Postgres: `COPY ... FROM STDIN WITH (FORMAT CSV)` via psycopg2
              `cursor.copy_expert`. Tipicamente 10-100x piu' veloce di
              executemany su dataset >10k righe.

Il path MySQL `LOAD DATA LOCAL INFILE` e' stato rimosso il 2026-04-23
(decisione pre-deadline: review §3.3 keep-optional, §4 A2, §5.2 ROI
basso; dettaglio in `kickoff.md §9.5`). Dialects MySQL/MariaDB cadono
ora nel raise `BulkInsertNotSupportedError(reason="dialect_not_supported")`
come Oracle/MSSQL/SQLite; il caller `_execute_batch` fa fallback a
executemany. Per MSSQL il boost e' `fast_executemany=True` sull'engine
factory (vedi `app/db/query_executor.py:_crea_engine_mssql`).

Scope:
  - Solo operation == "insert" (COPY non supporta UPSERT/UPDATE con
    ON CONFLICT).
  - Nessuna colonna geometry (richiederebbe ST_GeomFromText su staging,
    out of scope per la prima iterazione).
  - Fallback automatico ad executemany su qualsiasi exception del bulk
    path: il caller deve catchare `BulkInsertNotSupportedError` e
    riprovare con il path classico.

Type marshalling: pandas `df.to_csv()` produce CSV standard con quoting
QUOTE_MINIMAL. NULL diventa stringa vuota — riconosciuta come NULL dal
COPY via `NULL ''`.

Sicurezza: gli identificatori (schema, table, column names) vengono
quotati con la convenzione del dialect — niente SQL injection da nomi
tabella/colonna.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import List, TYPE_CHECKING

import pandas as pd

from app.utils.logger import get_request_id, get_user_id

if TYPE_CHECKING:
    from app.core.carica.models import ColumnMapping


logger = logging.getLogger(__name__)


class BulkInsertNotSupportedError(Exception):
    """Sollevata quando il bulk path non puo' essere usato per questa
    chiamata (driver assente, geometry presente, server-side disable,
    altro). Il caller deve fallback a executemany."""


# ─────────────────────────────────────────────────────────────────────
# Helpers comuni
# ─────────────────────────────────────────────────────────────────────


def _has_geometry(columns: List["ColumnMapping"]) -> bool:
    return any(c.is_geometry for c in columns if not c.skip)


def _ordered_dest_columns(columns: List["ColumnMapping"]) -> List[str]:
    """Ordine destinazione delle colonne attive (skip esclusi).
    L'ordine deve matchare quello delle colonne nel CSV streamato."""
    return [c.dest_column for c in columns if not c.skip]


def _ordered_source_columns(columns: List["ColumnMapping"]) -> List[str]:
    """Ordine sorgente: nome colonna nel DataFrame per ciascuna mappatura."""
    return [c.source_column for c in columns if not c.skip]


def _quote_pg_ident(ident: str) -> str:
    """Quota un identificatore Postgres rispettando il case e prevenendo
    injection. Raddoppia eventuali doppi apici interni."""
    return '"' + ident.replace('"', '""') + '"'


def _df_subset_for_bulk(
    df: pd.DataFrame, source_cols: List[str],
) -> pd.DataFrame:
    """Estrae le colonne sorgenti nell'ordine definito dalle mappature.
    Solleva ValueError se manca una colonna (errore programmatico, non
    un'exception "transient" da fallback)."""
    missing = [c for c in source_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"DataFrame manca colonne richieste dalle mappature: {missing}"
        )
    return df[source_cols]


# ─────────────────────────────────────────────────────────────────────
# POSTGRES: COPY FROM STDIN
# ─────────────────────────────────────────────────────────────────────


def bulk_copy_postgres(
    sa_conn,
    schema: str,
    table: str,
    columns: List["ColumnMapping"],
    df: pd.DataFrame,
) -> int:
    """Bulk INSERT su Postgres via `COPY ... FROM STDIN WITH (FORMAT CSV)`.

    Args:
        sa_conn: SQLAlchemy Connection (l'oggetto del context manager
            `engine.begin()`/`engine.connect()`). Usiamo `connection`
            per ottenere il DBAPI psycopg2.
        schema: nome schema (puo' essere None/vuoto → niente prefisso).
        table:  nome tabella.
        columns: mappature colonne attive (skip esclusi).
        df: DataFrame da inserire.

    Returns:
        Numero righe inserite (== len(df) se successo).

    Raises:
        BulkInsertNotSupportedError: psycopg2 non disponibile, geometry
            colonne presenti, o errore COPY non recuperabile (caller
            deve fallback a executemany).
    """
    if _has_geometry(columns):
        logger.info(
            "Bulk COPY non applicabile: colonne geometry presenti",
            extra={
                "event": "bulk.fallback",
                "reason": "geometry",
                "dialect": "postgres",
                "schema": schema,
                "table": table,
                "rows": len(df),
                "error_type": None,
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )
        raise BulkInsertNotSupportedError(
            "COPY non supportato con colonne geometry "
            "(richiederebbe ST_GeomFromText su staging)"
        )

    source_cols = _ordered_source_columns(columns)
    dest_cols = _ordered_dest_columns(columns)
    if not source_cols:
        raise BulkInsertNotSupportedError("Nessuna colonna attiva da scrivere")

    n_rows = len(df)
    if n_rows == 0:
        return 0

    subset = _df_subset_for_bulk(df, source_cols)

    # Costruisci CSV in memoria. pandas to_csv:
    # - na_rep='' → NULL diventa stringa vuota (riconosciuta da NULL '')
    # - quoting QUOTE_MINIMAL → quote solo se il valore contiene virgola/quote/newline
    # - encoding utf-8 esplicito (vedi test_csv_encoding_explicit_contract)
    buf = io.StringIO()
    subset.to_csv(
        buf,
        index=False,
        header=False,
        na_rep="",
        quoting=csv.QUOTE_MINIMAL,
        encoding="utf-8",
    )
    buf.seek(0)

    # Estrai connessione DBAPI grezza dal SA Connection per accedere a
    # cursor.copy_expert (psycopg2-specific).
    raw_conn = sa_conn.connection
    if not hasattr(raw_conn, "cursor"):
        logger.info(
            "Bulk COPY non applicabile: SA connection senza DBAPI cursor",
            extra={
                "event": "bulk.fallback",
                "reason": "driver_unsupported",
                "dialect": "postgres",
                "schema": schema,
                "table": table,
                "rows": n_rows,
                "error_type": None,
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )
        raise BulkInsertNotSupportedError(
            "SQLAlchemy connection non espone DBAPI cursor (driver insolito?)"
        )

    qualified = (
        f"{_quote_pg_ident(schema)}.{_quote_pg_ident(table)}"
        if schema else _quote_pg_ident(table)
    )
    cols_sql = ", ".join(_quote_pg_ident(c) for c in dest_cols)

    copy_sql = (
        f"COPY {qualified} ({cols_sql}) FROM STDIN "
        f"WITH (FORMAT CSV, NULL '', ENCODING 'UTF8')"
    )

    try:
        cur = raw_conn.cursor()
    except Exception as e:
        logger.warning(
            f"Bulk COPY: impossibile ottenere cursor DBAPI ({type(e).__name__}: {e})",
            extra={
                "event": "bulk.fallback",
                "reason": "cursor_acquisition_failure",
                "dialect": "postgres",
                "schema": schema,
                "table": table,
                "rows": n_rows,
                "error_type": type(e).__name__,
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )
        raise BulkInsertNotSupportedError(
            f"Impossibile ottenere cursor DBAPI: {type(e).__name__}: {e}"
        ) from e

    try:
        cur.copy_expert(copy_sql, buf)
    except AttributeError as e:
        # Driver non psycopg2 (es. asyncpg via SA non espone copy_expert)
        logger.info(
            f"Bulk COPY non applicabile: driver Postgres senza copy_expert ({e})",
            extra={
                "event": "bulk.fallback",
                "reason": "driver_unsupported",
                "dialect": "postgres",
                "schema": schema,
                "table": table,
                "rows": n_rows,
                "error_type": type(e).__name__,
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )
        raise BulkInsertNotSupportedError(
            f"Driver Postgres non supporta copy_expert: {e}"
        ) from e
    except Exception as e:
        # BadCopyFileFormat / DataError / qualsiasi DB error.
        # Convertito in BulkInsertNotSupportedError → caller riprova con
        # executemany (che dara' errore per-riga interpretabile).
        msg = (
            f"COPY fallito ({type(e).__name__}: {e}). "
            f"Fallback a executemany per ottenere errore per-riga."
        )
        logger.warning(
            msg,
            extra={
                "event": "bulk.fallback",
                "reason": "copy_error",
                "dialect": "postgres",
                "schema": schema,
                "table": table,
                "rows": n_rows,
                "error_type": type(e).__name__,
                "request_id": get_request_id(),
                "user_id": get_user_id(),
            },
        )
        raise BulkInsertNotSupportedError(msg) from e
    finally:
        try:
            cur.close()
        except Exception as close_err:
            logger.debug(
                "COPY cursor.close() failed: %s: %s",
                type(close_err).__name__,
                close_err,
            )

    logger.info(
        f"Bulk COPY completato: {n_rows} righe su "
        f"{schema + '.' if schema else ''}{table}",
        extra={
            "event": "bulk.success",
            "dialect": "postgres",
            "schema": schema,
            "table": table,
            "rows": n_rows,
            "protocol": "copy_from_stdin",
            "request_id": get_request_id(),
            "user_id": get_user_id(),
        },
    )
    return n_rows


# ─────────────────────────────────────────────────────────────────────
# Dispatch factory
# ─────────────────────────────────────────────────────────────────────


def bulk_insert(
    sa_conn,
    db_type: str,
    schema: str,
    table: str,
    columns: List["ColumnMapping"],
    df: pd.DataFrame,
) -> int:
    """Dispatcher: chiama il bulk writer specifico del dialect.

    Dialect supportati:
      - postgres / postgresql → COPY FROM STDIN
      - mysql / mariadb / oracle / mssql / sqlite → executemany (no bulk
        in-process). Per MSSQL il boost performance arriva da
        `fast_executemany=True` impostato all'engine factory
        (vedi `app/db/query_executor.py`), non da un bulk_writer
        dedicato. MySQL LOAD DATA LOCAL INFILE e' stato rimosso il
        2026-04-23 (kickoff.md §9.5; tombstoned nei contract test).

    Raises:
        BulkInsertNotSupportedError: se il dialect non e' supportato
            o se il caso specifico richiede fallback (geometry,
            server-side disable, ecc.)
    """
    dialect = (db_type or "").lower()
    if dialect in ("postgres", "postgresql"):
        return bulk_copy_postgres(sa_conn, schema, table, columns, df)
    logger.info(
        f"Bulk insert non implementato per dialect '{dialect}'",
        extra={
            "event": "bulk.fallback",
            "reason": "dialect_not_supported",
            "dialect": dialect,
            "schema": schema,
            "table": table,
            "rows": len(df),
            "error_type": None,
            "request_id": get_request_id(),
            "user_id": get_user_id(),
        },
    )
    raise BulkInsertNotSupportedError(
        f"Bulk insert non implementato per dialect '{dialect}'"
    )


# ─────────────────────────────────────────────────────────────────────
# Feature flag (opt-out via env)
# ─────────────────────────────────────────────────────────────────────


def is_bulk_enabled() -> bool:
    """Letta da env `CARICA_BULK_INSERT` (default `auto`).

    Valori:
      - `off` / `false` / `0` → disabilita, sempre executemany
      - `on` / `true` / `1` / `auto` (default) → attiva, fallback su
        BulkInsertNotSupportedError

    Permette disable rapido in caso di regressione operativa senza
    redeploy del codice (basta env var update + restart).
    """
    raw = (os.getenv("CARICA_BULK_INSERT") or "auto").strip().lower()
    return raw not in ("off", "false", "0", "no", "disabled")
