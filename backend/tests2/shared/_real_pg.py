"""PostgreSQL VERO per i test che una firma SQL non basta a garantire.

UI conferma finale (06/11/2026, nota «test su PG vero»): l'errore A-K1 era
passato perche' la query era verificata solo sulla firma. I test che
richiedono un PostgreSQL vero:

- leggono il DSN da ``PLENORA_REAL_PG_DSN`` e senza DSN si saltano col motivo;
- portano il marcatore ``real_pg`` (piu' ``integration``, escluso dal comando
  rapido): ``tests2/shared/test_real_pg_lane_contract.py`` vieta un test che
  usa il DSN senza il marcatore, cosi' il lanciatore li trova tutti;
- lavorano in uno schema temporaneo, rimosso alla fine.

Come lanciarli (un comando, container usa-e-getta):

    python backend/scripts/run_real_pg_tests.py
    # Docker dentro WSL:  python backend/scripts/run_real_pg_tests.py --docker "wsl -e docker"
    # PostgreSQL gia' acceso: PLENORA_REAL_PG_DSN=postgresql://... pytest -m real_pg tests2

LIMITE DICHIARATO IN CI: la pipeline non ha un PostgreSQL, quindi li salta;
in CI girano solo le firme SQL. Vanno lanciati a mano (o in un job con un
servizio postgres) prima di un rilascio che tocca query o migrazioni.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest

REAL_PG_DSN = os.environ.get("PLENORA_REAL_PG_DSN", "")

needs_real_pg = pytest.mark.skipif(
    not REAL_PG_DSN,
    reason="PLENORA_REAL_PG_DSN non impostato: nessun PostgreSQL vero (scripts/run_real_pg_tests.py)",
)


def real_pg_marks(func):
    """I tre marcatori di un test su PostgreSQL vero, in un colpo solo."""
    return pytest.mark.real_pg(pytest.mark.integration(needs_real_pg(func)))


def connect(*, autocommit: bool = True, search_path: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    # connect_timeout: un server sparito fa fallire subito, non appendere la suite.
    conn = psycopg.connect(REAL_PG_DSN, autocommit=autocommit, row_factory=dict_row, connect_timeout=10)
    if search_path:
        conn.execute(f"SET search_path TO {search_path}")
        if not autocommit:
            conn.commit()  # il SET resta per la sessione; nessuna transazione aperta
    return conn


@contextmanager
def real_pg_schema():
    """Uno schema temporaneo su un PostgreSQL vero, rimosso alla fine.

    Restituisce la connessione (autocommit, search_path sullo schema); il
    nome dello schema e' ``current_schema()`` su quella connessione.
    """
    schema = f"conferma_{uuid.uuid4().hex[:10]}"
    conn = connect()
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()
