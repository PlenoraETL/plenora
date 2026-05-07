"""
bulk_writers.py — Scaffolding dispatcher bulk insert (post-rimozione COPY PG).

Storico:
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
