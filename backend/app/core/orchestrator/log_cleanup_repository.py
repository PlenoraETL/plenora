"""Orchestrator cleanup PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any

def execute(sql: str, params: tuple[Any, ...] = ()):
    from app.db.pg_connection import execute as pg_execute

    return pg_execute(sql, params)
