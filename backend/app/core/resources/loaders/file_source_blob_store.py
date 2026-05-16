"""DB lookup boundary for the file-source blob resource loader."""

from __future__ import annotations

from typing import Mapping, Optional

from app.db.pg_connection import unit_of_work


def fetch_file_source_blob_lookup_row(
    sql: str,
    params: tuple,
) -> Optional[Mapping[str, object]]:
    """Fetch the file-source blob metadata/data row through the DB boundary."""
    with unit_of_work() as session:
        return session.fetchone(sql, params)
