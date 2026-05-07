"""Helper MariaDB reali per gli integration test."""

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
from tests2.integration.provider_config import provider_config, require_provider_enabled


_MARIADB_CONFIG = provider_config("mariadb", default_password="dataflow_test_2026")
MARIADB_HOST = _MARIADB_CONFIG.host
MARIADB_PORT = _MARIADB_CONFIG.port
MARIADB_DATABASE = _MARIADB_CONFIG.database
MARIADB_USER = _MARIADB_CONFIG.username
MARIADB_PASSWORD = _MARIADB_CONFIG.password


@contextmanager
def mariadb_conn() -> Iterator[pymysql.connections.Connection]:
    require_provider_enabled("MariaDB", _MARIADB_CONFIG)
    conn = pymysql.connect(
        host=MARIADB_HOST,
        port=MARIADB_PORT,
        database=MARIADB_DATABASE,
        user=MARIADB_USER,
        password=MARIADB_PASSWORD,
        autocommit=False,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def require_mariadb_enabled() -> None:
    require_provider_enabled("MariaDB", _MARIADB_CONFIG)


@contextmanager
def seeded_mariadb_table() -> Iterator[dict]:
    table_name = make_it_id("mariadb_src")
    with mariadb_conn() as conn:
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
        yield {"table_name": table_name}
    finally:
        with mariadb_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")
