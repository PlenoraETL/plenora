"""Helper Oracle reali per gli integration test."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import oracledb

from tests2.integration.harness.db import make_it_id


ORACLE_HOST = "127.0.0.1"
ORACLE_PORT = 1521
ORACLE_SERVICE = "FREEPDB1"
ORACLE_USER = "dataflow"
ORACLE_PASSWORD = "dataflow_test_2026"


@contextmanager
def oracle_conn() -> Iterator[oracledb.Connection]:
    conn = oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}",
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
