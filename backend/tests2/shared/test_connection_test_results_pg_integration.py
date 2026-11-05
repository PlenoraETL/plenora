"""
Esito del «Test connessione»: gli SQL dello store (``_RECORD_SQL``,
``_RESET_SQL``, ``_LOAD_SQL`` di ``app/shared/connection_tests/results.py``)
eseguiti su PostgreSQL reale, in una transazione annullata alla fine.

Un errore di programmazione SQL non e' piu' silenziato a runtime (log ERROR
e propagazione): questo test lo ferma prima. Si salta senza PG raggiungibile
o senza la tabella ``connection_test_results`` (lane no-DB).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.shared.connection_tests import results

pytestmark = [
    pytest.mark.integration,
    pytest.mark.shared,
    pytest.mark.contract,
    pytest.mark.requires_postgres,
]


def _pg_ready() -> bool:
    try:
        from app.db.pg_connection import get_connection, put_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM connection_test_results LIMIT 0")
                cur.execute("SELECT 1 FROM users LIMIT 0")
        finally:
            conn.rollback()
            put_connection(conn)
        return True
    except Exception:  # qualunque motivo: PG assente -> skip
        return False


@pytest.mark.skipif(not _pg_ready(), reason="Richiede PG con connection_test_results e users")
def test_store_sql_runs_on_postgresql():
    from app.db.pg_connection import get_connection, put_connection

    kind, rid = "connection", "it-connection-test-results"
    t0 = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(results._RECORD_SQL, (kind, rid, True, "ok", t0, "no-such-user", "mario", 12))
            cur.execute(results._LOAD_SQL, (kind, [rid]))
            (row,) = cur.fetchall()
            assert row["resource_id"] == rid and row["tested_by_name"] == "mario"
            assert row["tested_by_group"] is None  # utente inesistente
            cur.execute(results._RESET_SQL, (kind, rid, t0 + timedelta(seconds=1)))
            # Un test partito prima dell'azzeramento non sovrascrive.
            cur.execute(results._RECORD_SQL, (kind, rid, False, "ko", t0, None, None, 1))
            cur.execute(results._LOAD_SQL, (kind, [rid]))
            assert cur.fetchall() == []
    finally:
        conn.rollback()
        put_connection(conn)
