"""Helper PostgreSQL esterno reale per gli integration test."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus, urlparse
import uuid

# P0 (2026-05-26): migrato a psycopg3 (psycopg2 retirato).
# RealDictCursor (psycopg2.extras) → dict_row (psycopg.rows).
import psycopg
from psycopg.rows import dict_row


_HOST_RUN_HOST = "127.0.0.1"
_CONTAINER_SERVICE_HOST = "postgres"
_DEFAULT_PORT = 5432
_HOST_RUN_DATABASE = "dataflow_test"
_HOST_RUN_USER = "dataflow"
_HOST_RUN_PASSWORD = "dataflow_test_2026"


@dataclass(frozen=True)
class PostgresExtConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _first_env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _database_url_config() -> PostgresExtConfig | None:
    database_url = os.environ.get("PG_EXT_DATABASE_URL") or os.environ.get(
        "DATABASE_URL"
    )
    if not database_url:
        return None

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return None
    return PostgresExtConfig(
        host=parsed.hostname or _HOST_RUN_HOST,
        port=parsed.port or _DEFAULT_PORT,
        database=(parsed.path or "/").lstrip("/") or _HOST_RUN_DATABASE,
        user=parsed.username or _HOST_RUN_USER,
        password=parsed.password or _HOST_RUN_PASSWORD,
    )


def _parse_port(value: str | None) -> int:
    if not value:
        return _DEFAULT_PORT
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid PostgreSQL external test port {value!r}. "
            "Set PG_EXT_PORT/POSTGRES_PORT to an integer."
        ) from exc


def postgres_ext_config() -> PostgresExtConfig:
    url_config = _database_url_config()
    in_container = _running_in_container()

    return PostgresExtConfig(
        host=(
            _first_env_value("PG_EXT_HOST", "POSTGRES_HOST")
            or (url_config.host if url_config else None)
            or (_CONTAINER_SERVICE_HOST if in_container else _HOST_RUN_HOST)
        ),
        port=_parse_port(
            _first_env_value("PG_EXT_PORT", "POSTGRES_PORT")
            or (str(url_config.port) if url_config else None)
        ),
        database=(
            _first_env_value("PG_EXT_DATABASE", "PG_EXT_DB", "POSTGRES_DB")
            or (url_config.database if url_config else None)
            or _HOST_RUN_DATABASE
        ),
        user=(
            _first_env_value("PG_EXT_USER", "POSTGRES_USER")
            or (url_config.user if url_config else None)
            or _HOST_RUN_USER
        ),
        password=(
            _first_env_value("PG_EXT_PASSWORD", "POSTGRES_PASSWORD")
            or (url_config.password if url_config else None)
            or _HOST_RUN_PASSWORD
        ),
    )


def _connection_failure_message(config: PostgresExtConfig) -> str:
    return (
        "PostgreSQL external integration dependency is not reachable at "
        f"{config.user}@{config.host}:{config.port}/{config.database}. "
        "For host-run tests against published Docker ports, set e.g. "
        "PG_EXT_HOST=127.0.0.1 PG_EXT_PORT=5432 "
        "PG_EXT_DATABASE=dataflow_test PG_EXT_USER=dataflow "
        "PG_EXT_PASSWORD=... . Inside a backend container/Docker network, "
        "set PG_EXT_HOST to the service hostname, e.g. "
        "PG_EXT_HOST=postgres or PG_EXT_HOST=plenora-test-postgres, with "
        "matching PG_EXT_DATABASE/PG_EXT_USER/PG_EXT_PASSWORD; alternatively "
        "set PG_EXT_DATABASE_URL or DATABASE_URL."
    )


def postgres_ext_sqlalchemy_url() -> str:
    config = postgres_ext_config()
    return (
        "postgresql+psycopg2://"
        f"{quote_plus(config.user)}:{quote_plus(config.password)}@"
        f"{config.host}:{config.port}/{quote_plus(config.database)}"
    )


def postgres_ext_connection_payload() -> dict:
    config = postgres_ext_config()
    return {
        "host": config.host,
        "port": config.port,
        "database": config.database,
        "db_schema": "public",
        "username": config.user,
        "password": config.password,
    }


_CONFIG = postgres_ext_config()
PG_EXT_HOST = _CONFIG.host
PG_EXT_PORT = _CONFIG.port
PG_EXT_DATABASE = _CONFIG.database
PG_EXT_USER = _CONFIG.user
PG_EXT_PASSWORD = _CONFIG.password


@contextmanager
def postgres_ext_conn() -> Iterator[psycopg2.extensions.connection]:
    config = postgres_ext_config()
    try:
        conn = psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=config.password,
            connect_timeout=10,
            row_factory=dict_row,
        )
    except psycopg.OperationalError as exc:
        raise RuntimeError(_connection_failure_message(config)) from exc
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
    table_name = f"it_pg_src_{uuid.uuid4().hex[:10]}"
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
