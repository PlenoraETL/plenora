"""Helper PostgreSQL esterno reale per gli integration test."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from tests2.integration.harness.db import make_it_id


PG_EXT_HOST = "127.0.0.1"
PG_EXT_PORT = 5432
PG_EXT_DATABASE = "dataflow_test"
PG_EXT_USER = "dataflow"
PG_EXT_PASSWORD = "dataflow_test_2026"


@contextmanager
def postgres_ext_conn() -> Iterator[psycopg2.extensions.connection]:
    conn = psycopg2.connect(
        host=PG_EXT_HOST,
        port=PG_EXT_PORT,
        dbname=PG_EXT_DATABASE,
        user=PG_EXT_USER,
        password=PG_EXT_PASSWORD,
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,
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
def seeded_postgres_ext_table() -> Iterator[dict]:
    table_name = make_it_id("pg_src")
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE public.{table_name} (
                    id INTEGER PRIMARY KEY,
                    city TEXT NOT NULL,
                    score INTEGER NOT NULL
                )
                """
            )
            cur.execute(
                f"""
                INSERT INTO public.{table_name} (id, city, score)
                VALUES (%s, %s, %s), (%s, %s, %s)
                """,
                (1, "Rome", 10, 2, "Milan", 20),
            )
    try:
        yield {"table_name": table_name}
    finally:
        with postgres_ext_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS public.{table_name}")


def postgres_ext_fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def postgres_ext_execute(sql: str, params: tuple = ()) -> None:
    with postgres_ext_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
