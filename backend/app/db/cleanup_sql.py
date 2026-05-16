"""Low-level SQL primitives for lifecycle cleanup modules."""

from __future__ import annotations

from typing import Protocol

from app.db.pg_connection import unit_of_work


class _RowCountCursor(Protocol):
    rowcount: int


def rowcount(cursor: object, fallback: int = 0) -> int:
    """Return DB cursor rowcount without tying cleanup modules to driver classes."""
    value = getattr(cursor, "rowcount", fallback)
    return value if isinstance(value, int) else fallback


def delete_group_rows(sql: str, group_id: str) -> int:
    """Execute a domain-owned static DELETE ... WHERE group_id statement."""
    with unit_of_work() as session:
        return rowcount(session.execute(sql, (group_id,)))


__all__ = ["delete_group_rows", "rowcount"]
