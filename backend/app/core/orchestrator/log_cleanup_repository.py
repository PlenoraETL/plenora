"""Orchestrator cleanup PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any

import app.db.pg_connection as pg_connection


def execute(sql: str, params: tuple[Any, ...] = ()):
    return pg_connection.execute(sql, params)
