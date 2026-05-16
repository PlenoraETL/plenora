"""Repository boundary for result cleanup persistence operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.db.pg_connection import transaction as _transaction
from app.db.pg_connection import unit_of_work


def list_distinct_project_id_rows() -> list[Any]:
    """Return raw rows for projects that currently own result records."""
    with unit_of_work() as session:
        return session.fetchall("SELECT DISTINCT project_id FROM results")


def transaction() -> Iterator[Any]:
    """Open the DB transaction used by result cleanup lifecycle code."""
    return _transaction()
