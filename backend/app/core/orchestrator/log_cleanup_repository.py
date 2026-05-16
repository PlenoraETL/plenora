"""Orchestrator cleanup PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any

from app.db.pg_connection import unit_of_work


def execute(sql: str, params: tuple[Any, ...] = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)
