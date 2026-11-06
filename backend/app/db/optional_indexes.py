"""Indici facoltativi: ottimizzazioni create DOPO l'avvio, mai all'avvio.

Semplificazione H1–H7 (06/11/2026, H5). Un indice che serve solo a fare
prima (non a far funzionare il codice) non e' una migrazione: un
CREATE INDEX CONCURRENTLY aspetta la fine delle transazioni aperte, e
all'avvio quell'attesa bloccava il backend (controllo di prontezza fallito,
riavvii in ciclo). Le colonne e le tabelle che il codice usa restano
migrazioni normali (veloci, in transazione, app/db/migrations_runner).

Qui c'e' la tabella degli indici facoltativi; li crea il beat
(workers/beat_scheduler._ensure_optional_indexes, dal leader, quindi da UN
processo) in un giro di manutenzione:

- indice valido -> niente;
- costruzione in corso (pg_stat_progress_create_index) -> niente, al giro dopo;
- indice INVALID (costruzione interrotta) -> DROP INDEX CONCURRENTLY, poi lo ricrea;
- ogni istruzione aspetta i lock al piu' LOCK_TIMEOUT: oltre, rinuncia e
  riprova al giro dopo. Nessuna attesa lunga, nessun errore che fermi altro.

Connessione DEDICATA in autocommit, chiusa alla fine (H6): nessuno stato di
sessione (lock_timeout, autocommit) torna nel pool.
"""

from __future__ import annotations

from typing import Callable, Dict, List

import psycopg
from psycopg import sql as _sql

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Attesa massima di ogni istruzione sui lock (poi si riprova al giro dopo).
LOCK_TIMEOUT = "5s"

# nome -> (tabella, colonne). UI-1 (audit C25): GET /pipeline-runs/
# latest-by-project ordina log_runs per progetto e inizio; l'indice col gruppo
# in testa serve agli utenti non superadmin, che filtrano anche per gruppo.
OPTIONAL_INDEXES: Dict[str, tuple[str, str]] = {
    "idx_log_runs_project_ts_start": ("log_runs", "project_id, ts_start DESC"),
    "idx_log_runs_group_project_ts_start": ("log_runs", "group_id, project_id, ts_start DESC"),
}


def _index_state(conn: psycopg.Connection, name: str) -> str:
    """'valid' | 'building' | 'invalid' | 'missing'."""
    row = conn.execute(
        "SELECT i.indisvalid AS valid, EXISTS ("
        " SELECT 1 FROM pg_stat_progress_create_index p WHERE p.index_relid = i.indexrelid"
        ") AS building FROM pg_index i WHERE i.indexrelid = to_regclass(%s)",
        (name,),
    ).fetchone()
    if row is None:
        return "missing"
    valid, building = (row["valid"], row["building"]) if isinstance(row, dict) else row
    if valid:
        return "valid"
    return "building" if building else "invalid"


def ensure_optional_indexes(connect: Callable[[], psycopg.Connection]) -> List[str]:
    """Un giro: crea gli indici mancanti. Restituisce quelli creati ora.

    ``connect`` apre una connessione NUOVA in autocommit (la chiude questa
    funzione). Un lock non ottenuto entro LOCK_TIMEOUT non e' un errore: si
    riprova al giro dopo.
    """
    created: List[str] = []
    conn = connect()
    try:
        conn.execute(_sql.SQL("SET lock_timeout = {}").format(_sql.Literal(LOCK_TIMEOUT)))
        for name, (table, columns) in OPTIONAL_INDEXES.items():
            state = _index_state(conn, name)
            if state in ("valid", "building"):
                continue
            ident = _sql.Identifier(name)
            try:
                if state == "invalid":
                    conn.execute(_sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(ident))
                conn.execute(_sql.SQL("CREATE INDEX CONCURRENTLY IF NOT EXISTS {} ON {} ({})").format(
                    ident, _sql.Identifier(table), _sql.SQL(columns),
                ))
                created.append(name)
                logger.info(f"[INDEXES] creato {name}")
            except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled):
                # Transazioni aperte oltre LOCK_TIMEOUT: l'eventuale indice
                # INVALID lo toglie il giro dopo, prima di ricrearlo.
                logger.info(f"[INDEXES] {name}: lock non ottenuto entro {LOCK_TIMEOUT}, si riprova al giro dopo")
    finally:
        conn.close()
    return created


__all__ = ["LOCK_TIMEOUT", "OPTIONAL_INDEXES", "ensure_optional_indexes"]
