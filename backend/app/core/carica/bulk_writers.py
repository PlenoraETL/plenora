"""
bulk_writers.py — Bulk INSERT nativi per Postgres (COPY) e MySQL (LOAD DATA).

Sostituiscono `conn.execute(text(sql), params_list)` (executemany SQLAlchemy)
sui dialect dove esiste un protocollo bulk piu' efficiente:

  - Postgres: `COPY ... FROM STDIN WITH (FORMAT CSV)` via psycopg2
              `cursor.copy_expert`. Tipicamente 10-100x piu' veloce di
              executemany su dataset >10k righe.
  - MySQL:    `LOAD DATA LOCAL INFILE` via tempfile CSV. 5-50x piu' veloce
              di executemany. Richiede `local_infile=ON` server-side e
              connection string con `local_infile=1`.

Scope:
  - Solo operation == "insert" (COPY/LOAD DATA non supportano UPSERT/UPDATE
    con clausole ON CONFLICT/ON DUPLICATE KEY UPDATE).
  - Nessuna colonna geometry (richiederebbe ST_GeomFromText su staging,
    out of scope per la prima iterazione).
  - Fallback automatico ad executemany su qualsiasi exception del bulk
    path: il caller deve catchare `BulkInsertNotSupportedError` e
    riprovare con il path classico.

Type marshalling: pandas `df.to_csv()` produce CSV standard con quoting
QUOTE_MINIMAL. NULL diventa stringa vuota — riconosciuta come NULL dal
COPY/LOAD DATA via `NULL ''` (Postgres) e `\\N` (MySQL).

Sicurezza: gli identificatori (schema, table, column names) vengono
quotati con la convenzione del dialect — niente SQL injection da nomi
tabella/colonna.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import tempfile
from typing import List, TYPE_CHECKING

import pandas as pd

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


def _quote_mysql_ident(ident: str) -> str:
    """Quota un identificatore MySQL con backtick. Raddoppia backtick
    interni (caso patologico ma valido nel SQL standard MySQL)."""
    return "`" + ident.replace("`", "``") + "`"


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
        raise BulkInsertNotSupportedError(
            f"Impossibile ottenere cursor DBAPI: {type(e).__name__}: {e}"
        ) from e

    try:
        cur.copy_expert(copy_sql, buf)
    except AttributeError as e:
        # Driver non psycopg2 (es. asyncpg via SA non espone copy_expert)
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
        logger.warning(msg)
        raise BulkInsertNotSupportedError(msg) from e
    finally:
        try:
            cur.close()
        except Exception:
            pass

    return n_rows


# ─────────────────────────────────────────────────────────────────────
# MYSQL: LOAD DATA LOCAL INFILE
# ─────────────────────────────────────────────────────────────────────


# MySQL error code per "LOAD DATA LOCAL INFILE not allowed"
_MYSQL_LOCAL_INFILE_DISABLED = 1148


def bulk_load_mysql(
    sa_conn,
    schema: str,
    table: str,
    columns: List["ColumnMapping"],
    df: pd.DataFrame,
) -> int:
    """Bulk INSERT su MySQL via `LOAD DATA LOCAL INFILE`.

    Strategia: scrive un CSV su tempfile (LOCAL = file lato client,
    inviato sul wire), esegue LOAD DATA, cancella il tempfile.

    Args / Returns / Raises: come bulk_copy_postgres.

    Server prerequisiti:
      - `local_infile=ON` (mysqld config). Se OFF → error 1148 → raise
        BulkInsertNotSupportedError.
      - connection string SQLAlchemy DEVE includere `local_infile=1`,
        altrimenti il driver pymysql/mysqlclient rifiuta lato client.
    """
    if _has_geometry(columns):
        raise BulkInsertNotSupportedError(
            "LOAD DATA non supportato con colonne geometry"
        )

    source_cols = _ordered_source_columns(columns)
    dest_cols = _ordered_dest_columns(columns)
    if not source_cols:
        raise BulkInsertNotSupportedError("Nessuna colonna attiva da scrivere")

    n_rows = len(df)
    if n_rows == 0:
        return 0

    subset = _df_subset_for_bulk(df, source_cols)

    # Scrivi CSV su tempfile. Mantieni il path semplice (no Unicode
    # weirdness) usando suffisso esplicito.
    fd, tmp_path = tempfile.mkstemp(suffix=".csv", prefix="urby_bulk_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            subset.to_csv(
                fh,
                index=False,
                header=False,
                # MySQL LOAD DATA default: \\N per NULL — usiamo questo
                na_rep="\\N",
                quoting=csv.QUOTE_MINIMAL,
                encoding="utf-8",
            )

        qualified = (
            f"{_quote_mysql_ident(schema)}.{_quote_mysql_ident(table)}"
            if schema else _quote_mysql_ident(table)
        )
        cols_sql = ", ".join(_quote_mysql_ident(c) for c in dest_cols)

        # LOAD DATA path: MySQL vuole forward-slash anche su Windows
        load_path = tmp_path.replace("\\", "/")
        load_sql = (
            f"LOAD DATA LOCAL INFILE '{load_path}' "
            f"INTO TABLE {qualified} "
            f"CHARACTER SET utf8mb4 "
            f"FIELDS TERMINATED BY ',' "
            f"OPTIONALLY ENCLOSED BY '\"' "
            f"ESCAPED BY '\\\\' "
            f"LINES TERMINATED BY '\\n' "
            f"({cols_sql})"
        )

        raw_conn = sa_conn.connection
        try:
            cur = raw_conn.cursor()
        except Exception as e:
            raise BulkInsertNotSupportedError(
                f"Impossibile ottenere cursor DBAPI: {type(e).__name__}: {e}"
            ) from e

        try:
            cur.execute(load_sql)
        except Exception as e:
            # Detect local_infile disabled (error 1148)
            err_str = str(e).lower()
            if (
                "1148" in err_str
                or "local infile" in err_str
                or "loading local data" in err_str
            ):
                raise BulkInsertNotSupportedError(
                    f"MySQL LOCAL INFILE disabilitato server-side: {e}"
                ) from e
            logger.warning(
                f"LOAD DATA fallito ({type(e).__name__}: {e}). "
                f"Fallback a executemany."
            )
            raise BulkInsertNotSupportedError(str(e)) from e
        finally:
            try:
                cur.close()
            except Exception:
                pass

        return n_rows
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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
      - mysql / mariadb       → LOAD DATA LOCAL INFILE (stesso protocollo,
        MariaDB usa il driver `pymysql`/`mysqlclient` come MySQL e
        accetta la stessa sintassi LOAD DATA)
      - oracle / mssql / sqlite → executemany (no bulk in-process). Per
        MSSQL il boost performance arriva da `fast_executemany=True`
        impostato all'engine factory (vedi `app/db/query_executor.py`),
        non da un bulk_writer dedicato.

    Raises:
        BulkInsertNotSupportedError: se il dialect non e' supportato
            o se il caso specifico richiede fallback (geometry,
            server-side disable, ecc.)
    """
    dialect = (db_type or "").lower()
    if dialect in ("postgres", "postgresql"):
        return bulk_copy_postgres(sa_conn, schema, table, columns, df)
    if dialect in ("mysql", "mariadb"):
        return bulk_load_mysql(sa_conn, schema, table, columns, df)
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
