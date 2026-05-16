"""Orchestrator execution-log PostgreSQL repository boundary."""

from __future__ import annotations

from typing import Any

from app.shared.contracts.payloads import JsonRows
from app.db.pg_connection import unit_of_work


def execute(sql: str, params: tuple[Any, ...] = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> JsonRows:
    with unit_of_work() as session:
        return session.fetchall(sql, params)
