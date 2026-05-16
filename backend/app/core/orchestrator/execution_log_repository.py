"""Orchestrator execution-log PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any

import app.db.pg_connection as pg_connection
from app.shared.contracts.payloads import JsonRows


def execute(sql: str, params: tuple[Any, ...] = ()):
    return pg_connection.execute(sql, params)


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> JsonRows:
    return pg_connection.fetchall(sql, params)
