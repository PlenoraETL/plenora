"""
bulk_writers.py — Scaffolding dispatcher bulk insert (post-rimozione COPY PG).

Storico:
  - 2026-04-10 circa: implementati bulk nativi per Postgres (`COPY FROM STDIN`
    via psycopg2) e MySQL (`LOAD DATA LOCAL INFILE` via tempfile CSV).
  - 2026-04-23: entrambi i path rimossi.
      * MySQL LOAD DATA: decisione pre-deadline (`kickoff.md §9.5`)
      * Postgres COPY: decisione diretta accettando regressione
        user-visible ~19x (`kickoff.md §9.6`, deviazione esplicita da
        `performance_bulk_review.md §5.1` no-go)

Stato attuale:
  Tutti i dialect (postgres, postgresql, mysql, mariadb, oracle, mssql,
  sqlite) cadono nel raise `BulkInsertNotSupportedError(reason=
  "dialect_not_supported")`. Il caller `_execute_batch` in
  `database_writer.py` cattura e fa fallback a `conn.execute` con lista
  `params_list` (executemany SQLAlchemy), path unico post-rimozione.

Path alternativi ancora attivi:
  - MSSQL: `fast_executemany=True` sull'engine factory
    (`app/db/query_executor.py::_crea_engine_mssql`) — driver-level,
    non bulk writer dedicato. Non toccato dalle rimozioni 2026-04-23.

Kill-switch:
  `CARICA_BULK_INSERT` env preservato come scaffolding per retro-compat
  operativa. Post-rimozione e' funzionalmente no-op (il bulk non
  esiste piu'), ma la funzione `is_bulk_enabled()` resta letta dal
  wiring in `_execute_batch` per decidere se tentare il dispatcher (che
  raise sempre) o saltare direttamente ad executemany.

Telemetria residua:
  Il dispatcher emette `event=bulk.fallback reason=dialect_not_supported`
  con correlation IDs (request_id/user_id) su ogni invocazione. Utile
  per observability post-deploy: confermera' che il path executemany
  e' l'unico effettivamente in uso.
"""

from __future__ import annotations

import logging
import os

from app.utils.logger import get_request_id, get_user_id


logger = logging.getLogger(__name__)


class BulkInsertNotSupportedError(Exception):
    """Sollevata dal dispatcher quando il bulk path non e' disponibile.
    Post-rimozione 2026-04-23 e' raise per tutti i dialect. Il caller
    (`_execute_batch`) catcha e fa fallback a executemany."""


# Dispatch factory (post-rimozione: raise sempre dialect_not_supported)


def bulk_insert(
    sa_conn,
    db_type: str,
    schema: str,
    table: str,
    columns,
    df,
) -> int:
    """Dispatcher scaffolding post-rimozione.

    Storicamente dispatchava su `bulk_copy_postgres` (COPY FROM STDIN)
    per PG/PostgreSQL e su `bulk_load_mysql` (LOAD DATA LOCAL INFILE)
    per MySQL/MariaDB. Entrambe le funzioni sono state rimosse il
    2026-04-23 (`kickoff.md §9.5` + `§9.6`). Post-rimozione:

      - Tutti i dialect cadono nel raise `BulkInsertNotSupportedError
        (reason="dialect_not_supported")`
      - Il caller `_execute_batch` fa fallback automatico a executemany
      - Telemetria `event=bulk.fallback reason=dialect_not_supported`
        e' emessa su ogni invocazione (correlation IDs dai ContextVar)

    Raises:
        BulkInsertNotSupportedError: sempre, post-rimozione.
    """
    dialect = (db_type or "").lower()
    logger.info(
        f"Bulk insert non implementato per dialect '{dialect}' "
        f"(post-rimozione 2026-04-23; vedi kickoff.md §9.5, §9.6)",
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


# Feature flag (opt-out via env) — scaffolding preservato


def is_bulk_enabled() -> bool:
    """Letta da env `CARICA_BULK_INSERT` (default `auto`).

    Valori:
      - `off` / `false` / `0` → disabilita, il wiring salta direttamente
        a executemany senza invocare il dispatcher
      - `on` / `true` / `1` / `auto` (default) → il wiring invoca il
        dispatcher, che post-rimozione 2026-04-23 raise sempre
        `dialect_not_supported` → fallback executemany

    Post-rimozione e' funzionalmente no-op (entrambi i valori producono
    executemany). Preservato come scaffolding per retro-compat operativa
    e per evitare scope-creep sulla rimozione dell'env var.
    """
    # BE-CONFIG-001D: default tipato in
    # `Settings.DEFAULTS["CARICA_BULK_INSERT"]="auto"`. Override env
    # continua a funzionare via Settings.
    from app.config import settings as _settings
    raw = (_settings.get("CARICA_BULK_INSERT") or "auto").strip().lower()
    return raw not in ("off", "false", "0", "no", "disabled")
