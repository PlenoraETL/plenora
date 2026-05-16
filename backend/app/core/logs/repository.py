"""Logs-owned PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any, TypeAlias

import app.db.pg_connection as pg_connection


LogRepositoryRow: TypeAlias = dict[str, Any]


def fetchone(sql: str, params: tuple[Any, ...] = ()) -> LogRepositoryRow | None:
    return pg_connection.fetchone(sql, params)


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[LogRepositoryRow]:
    return pg_connection.fetchall(sql, params)
