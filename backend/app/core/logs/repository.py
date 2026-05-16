"""Logs-owned PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any, TypeAlias

from app.db.pg_connection import unit_of_work


LogRepositoryRow: TypeAlias = dict[str, Any]


def fetchone(sql: str, params: tuple[Any, ...] = ()) -> LogRepositoryRow | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[LogRepositoryRow]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)
