"""Helper MySQL reali per gli integration test."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

# Skip pulito a collection time se pymysql non e' installato (es. CI runner
# senza driver DB optional). I test integration che dipendono da questo
# harness vengono saltati con messaggio esplicito invece di rompere la
# collection con ModuleNotFoundError. Allineato con intent workflow CI:
# i test integration si auto-skippano via guard interno (_<dialect>_
# reachable), ma l'ImportError esploderebbe prima del guard.
pytest.importorskip("pymysql")

import pymysql  # noqa: E402

from tests2.integration.harness.db import make_it_id


MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_DATABASE = "dataflow_test"
MYSQL_USER = "dataflow"
MYSQL_PASSWORD = "dataflow_test_2026"


@contextmanager
def mysql_conn() -> Iterator[pymysql.connections.Connection]:
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            autocommit=False,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except pymysql.err.OperationalError as exc:
        pytest.skip(f"MySQL non raggiungibile: {exc}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def seeded_mysql_table() -> Iterator[dict]:
    """Crea una tabella MySQL temporanea con due righe e la elimina a fine test."""
    table_name = make_it_id("mysql_src")
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE `{table_name}` (
                    id INT PRIMARY KEY,
                    city VARCHAR(255) NOT NULL,
                    score INT NOT NULL
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO `{table_name}` (id, city, score)
                VALUES (%s, %s, %s), (%s, %s, %s)
                """,
                (1, "Rome", 10, 2, "Milan", 20),
            )
    try:
        yield {
            "table_name": table_name,
            "rows": [
                {"id": 1, "city": "Rome", "score": 10},
                {"id": 2, "city": "Milan", "score": 20},
            ],
        }
    finally:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")


def mysql_fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def mysql_execute(sql: str, params: tuple = ()) -> None:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
