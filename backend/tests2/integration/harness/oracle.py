"""Helper Oracle reali per gli integration test."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

import pytest

# Skip pulito a collection time se oracledb non e' installato (es. CI
# runner senza driver DB optional). I test integration che dipendono da
# questo harness vengono saltati con messaggio esplicito invece di
# rompere la collection con ModuleNotFoundError. Allineato con intent
# workflow CI: i test integration si auto-skippano via guard interno
# (_<dialect>_reachable), ma l'ImportError esploderebbe prima del guard.
pytest.importorskip("oracledb")

import oracledb  # noqa: E402

from tests2.integration.harness.db import make_it_id


T = TypeVar("T")


ORACLE_HOST = "127.0.0.1"
ORACLE_PORT = 1521
ORACLE_SERVICE = "FREEPDB1"
ORACLE_USER = "dataflow"
ORACLE_PASSWORD = "dataflow_test_2026"

# DPY-4011: il server Oracle chiude la connessione durante l'handshake/query.
# Succede sotto pressione di rapid-connect (integration suite che apre/chiude
# decine di connessioni in sequenza). Retry con backoff a livello harness —
# non e' un bug applicativo, e' flake dell'Oracle thin client in quella
# condizione.
_CONNECT_RETRIES = 5
_CONNECT_BACKOFF_S = 1.0


def _connect_with_retry() -> oracledb.Connection:
    last_err: Exception | None = None
    for attempt in range(_CONNECT_RETRIES):
        try:
            return oracledb.connect(
                user=ORACLE_USER,
                password=ORACLE_PASSWORD,
                dsn=f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}",
            )
        except oracledb.DatabaseError as e:
            last_err = e
            if "DPY-4011" not in str(e) and "DPY-6005" not in str(e):
                raise
            time.sleep(_CONNECT_BACKOFF_S * (2 ** attempt))
    assert last_err is not None
    raise last_err


@contextmanager
def oracle_conn() -> Iterator[oracledb.Connection]:
    conn = _connect_with_retry()
    try:
        yield conn
        try:
            conn.commit()
        except oracledb.DatabaseError:
            # Commit su connessione droppata dal server (DPY-4011/1001):
            # il work e' gia' perso. Ignoriamo per non mascherare lo stato
            # dei test downstream con un errore di commit.
            pass
    except Exception:
        # Rollback best-effort: se la connessione e' gia' morta (DPY-1001),
        # non vogliamo mascherare l'eccezione originale del test con un
        # InterfaceError del rollback.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def oracle_read_retry(op: Callable[[oracledb.Connection], T]) -> T:
    """Esegue una read-only op Oracle con retry su DPY-4011.

    Pensato per helper dei test che leggono metadati (user_constraints,
    user_indexes, SELECT COUNT(*)): pure letture senza side-effect, sicure
    da rieseguire su connessione fresca se Oracle drop-pa il socket a meta'
    query. Non usare per DDL/DML — quelle vanno con `oracle_conn()`
    direttamente, retry non sicuro."""
    last_err: Exception | None = None
    for attempt in range(_CONNECT_RETRIES):
        try:
            with oracle_conn() as conn:
                return op(conn)
        except oracledb.DatabaseError as e:
            last_err = e
            msg = str(e)
            if "DPY-4011" not in msg and "DPY-1001" not in msg and "DPY-6005" not in msg:
                raise
            time.sleep(_CONNECT_BACKOFF_S * (2 ** attempt))
    assert last_err is not None
    raise last_err


@contextmanager
def seeded_oracle_table() -> Iterator[dict]:
    table_name = make_it_id("ora_src").upper()
    with oracle_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE {table_name} (
                id NUMBER PRIMARY KEY,
                city VARCHAR2(255) NOT NULL,
                score NUMBER NOT NULL
            )
            """
        )
        cur.executemany(
            f"INSERT INTO {table_name} (id, city, score) VALUES (:1, :2, :3)",
            [(1, "Rome", 10), (2, "Milan", 20)],
        )
        cur.close()
    try:
        yield {"table_name": table_name}
    finally:
        with oracle_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(f"DROP TABLE {table_name} PURGE")
            finally:
                cur.close()
