"""Logs-owned PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any

import app.db.pg_connection as pg_connection


def fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    return pg_connection.fetchone(sql, params)


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return pg_connection.fetchall(sql, params)
