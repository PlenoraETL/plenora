"""
migrations_runner.py — Applicazione e tracking delle migration PostgreSQL.

Motivazione (C8-nit)
====================
Fino a C8, le migration in `migrations/*.sql` erano applicate
manualmente via `psql -f ...` e non c'era tracking di quali fossero
gia' state applicate. L'endpoint `/ready` non poteva quindi dire
"schema allineato" o "migration X pendente".

Questo runner risolve con:
- Tabella `plenora.schema_migrations(filename TEXT PRIMARY KEY, applied_at)`
- All'avvio dell'app: scan di `migrations/*.sql`, esegue quelle non
  ancora registrate, inserisce una riga per ognuna.
- Le migration esistenti usano `CREATE ... IF NOT EXISTS` → rieseguirle
  e' no-op, quindi il primo run post-deploy non rompe nulla.
- `/ready` compara files vs righe nella tabella → 503 se ci sono
  pending.

Convenzioni
===========
- File naming: `YYYY-MM-DD_description.sql` (ordinamento lessicografico
  corrisponde all'ordine cronologico)
- Ogni file deve essere idempotente (IF NOT EXISTS / IF EXISTS) —
  cosi' un re-run accidentale non rompe la produzione.
- Se una migration fallisce, il runner solleva e l'app non parte:
  meglio un crash loud che una prod con schema inconsistente.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from app.utils.logger import get_logger


logger = get_logger(__name__)


# Directory relativa al backend root (parent di app/)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plenora.schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def list_migration_files() -> List[str]:
    """Ritorna i nomi file `.sql` in MIGRATIONS_DIR, ordinati lessicograficamente."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in MIGRATIONS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() == ".sql"
    )


def list_applied_migrations() -> List[str]:
    """Legge le migration gia' applicate da plenora.schema_migrations."""
    # Import locale per evitare side-effect al load del modulo
    from app.db.pg_connection import fetchall
    try:
        rows = fetchall(
            "SELECT filename FROM plenora.schema_migrations ORDER BY filename"
        )
    except Exception as e:
        # Se la tabella non esiste ancora (primo bootstrap), ritorna [].
        # Il runner la creera' subito dopo.
        logger.warning(
            f"schema_migrations non leggibile (primo bootstrap?): {e}"
        )
        return []
    return [r["filename"] for r in rows]


def list_pending_migrations() -> List[str]:
    """Ritorna i file non ancora registrati in schema_migrations."""
    files = set(list_migration_files())
    applied = set(list_applied_migrations())
    return sorted(files - applied)


def apply_pending_migrations() -> List[str]:
    """Applica e registra tutte le migration pending. Return l'elenco
    dei file applicati in questa chiamata.

    Raises:
        Exception se una migration fallisce (l'app non deve partire
        con schema inconsistente).
    """
    # Import locale (pg_connection potrebbe non essere init-ato al module load)
    from app.db.pg_connection import execute, get_connection, put_connection

    # Crea la tabella di tracking se non esiste (idempotente)
    execute(_CREATE_TABLE_SQL)

    pending = list_pending_migrations()
    if not pending:
        logger.info("[MIGRATIONS] Nessuna migration pending")
        return []

    applied_now: List[str] = []
    for filename in pending:
        filepath = MIGRATIONS_DIR / filename
        try:
            sql = filepath.read_text(encoding="utf-8")
        except Exception as e:
            raise RuntimeError(
                f"[MIGRATIONS] impossibile leggere {filename}: {e}"
            ) from e

        logger.info(f"[MIGRATIONS] Applico: {filename}")
        # Eseguiamo la migration + l'INSERT di tracking in UNA sola
        # connection con commit atomico. Se la migration fallisce,
        # il rollback copre anche il tracking (non marchiamo come
        # applicata una migration che ha fallito).
        conn = get_connection()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO plenora.schema_migrations (filename) VALUES (%s) "
                    "ON CONFLICT (filename) DO NOTHING",
                    (filename,),
                )
            conn.commit()
            applied_now.append(filename)
            logger.info(f"[MIGRATIONS] OK: {filename}")
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise RuntimeError(
                f"[MIGRATIONS] fallita {filename}: {e}"
            ) from e
        finally:
            try:
                conn.autocommit = True
            except Exception:
                pass
            put_connection(conn)

    logger.info(
        f"[MIGRATIONS] Completato: {len(applied_now)} migration applicate"
    )
    return applied_now
