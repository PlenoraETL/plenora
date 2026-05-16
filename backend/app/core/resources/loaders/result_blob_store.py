"""DB lookup boundary for the result-blob resource loader."""

from __future__ import annotations

from typing import Mapping, Optional

from app.db.pg_connection import unit_of_work


def fetch_result_blob_lookup_row(
    sql: str,
    params: tuple,
) -> Optional[Mapping[str, object]]:
    """Fetch the result blob metadata/data row through the DB boundary."""
    with unit_of_work() as session:
        return session.fetchone(sql, params)
