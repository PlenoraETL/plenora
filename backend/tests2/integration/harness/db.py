"""
Helper DB reali per gli integration test.

Usano lo stesso connection layer dell'app invece di un DSN hardcoded,
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from app.db.pg_connection import get_connection, put_connection


def make_it_id(prefix: str) -> str:
    return f"it_{prefix}_{uuid.uuid4().hex[:10]}"


@contextmanager
def pg_conn() -> Iterator[Any]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def pg_fetchone(sql: str, params: tuple = ()) -> Optional[dict]:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def pg_execute(sql: str, params: tuple = ()) -> int:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
