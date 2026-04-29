"""Helper SQL Server reali per gli integration test."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

# Skip pulito a collection time se pyodbc non e' installato (es. CI runner
# senza driver DB optional). I test integration che dipendono da questo
# harness vengono saltati con messaggio esplicito invece di rompere la
# collection con ModuleNotFoundError. Allineato con intent workflow CI:
# i test integration si auto-skippano via guard interno (_<dialect>_
# reachable), ma l'ImportError esploderebbe prima del guard.
pytest.importorskip("pyodbc")

import pyodbc  # noqa: E402

from tests2.integration.harness.db import make_it_id


SQLSERVER_HOST = "127.0.0.1"
SQLSERVER_PORT = 1433
SQLSERVER_DATABASE = "dataflow_test"
SQLSERVER_USER = "sa"
SQLSERVER_PASSWORD = "DataFlow_Test_2026!"
SQLSERVER_DRIVER = "ODBC Driver 18 for SQL Server"
SQLSERVER_DRIVER_FALLBACK = "ODBC Driver 17 for SQL Server"

def _conn_str_candidates(database: str = SQLSERVER_DATABASE) -> list[str]:
    base = (
        f"SERVER={SQLSERVER_HOST},{SQLSERVER_PORT};"
        f"DATABASE={database};"
        f"UID={SQLSERVER_USER};"
        f"PWD={SQLSERVER_PASSWORD};"
        "TrustServerCertificate=yes;"
    )
    return [
        f"DRIVER={{{SQLSERVER_DRIVER}}};{base}Encrypt=Optional",
        f"DRIVER={{{SQLSERVER_DRIVER}}};{base}Encrypt=No",
        f"DRIVER={{{SQLSERVER_DRIVER_FALLBACK}}};{base}",
    ]


def sqlalchemy_conn_url_candidates(database: str = SQLSERVER_DATABASE) -> list[str]:
    """URL SQLAlchemy candidate allineati al probe pyodbc del harness."""
    from urllib.parse import quote_plus

    return [
        f"mssql+pyodbc:///?odbc_connect={quote_plus(cs)}"
        for cs in _conn_str_candidates(database)
    ]


@contextmanager
def sqlserver_conn() -> Iterator[pyodbc.Connection]:
    last_error = None
    conn = None
    for conn_str in _conn_str_candidates():
        try:
            conn = pyodbc.connect(conn_str, timeout=10)
            break
        except pyodbc.Error as e:
            last_error = e
            conn = None

    if conn is None:
        pytest.skip(
            f"SQL Server non raggiungibile o driver non compatibile: {last_error}",
            allow_module_level=True,
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
def seeded_sqlserver_table() -> Iterator[dict]:
    """Crea una tabella SQL Server temporanea con due righe e la elimina a fine test."""
    table_name = make_it_id("mssql_src")
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE dbo.[{table_name}] (
                id INT PRIMARY KEY,
                city NVARCHAR(255) NOT NULL,
                score INT NOT NULL
            )
            """
        )
        cur.execute(
            f"""
            INSERT INTO dbo.[{table_name}] (id, city, score)
            VALUES (?, ?, ?), (?, ?, ?)
            """,
            (1, "Rome", 10, 2, "Milan", 20),
        )
        cur.close()
    try:
        yield {
            "table_name": table_name,
            "rows": [
                {"id": 1, "city": "Rome", "score": 10},
                {"id": 2, "city": "Milan", "score": 20},
            ],
        }
    finally:
        with sqlserver_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"IF OBJECT_ID('dbo.[{table_name}]', 'U') IS NOT NULL DROP TABLE dbo.[{table_name}]")
            cur.close()


def sqlserver_fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        columns = [col[0] for col in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        return rows


def sqlserver_execute(sql: str, params: tuple = ()) -> None:
    with sqlserver_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        cur.close()
