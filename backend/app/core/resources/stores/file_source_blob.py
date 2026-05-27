"""DB lookup boundary for the file-source blob resource loader."""

from __future__ import annotations

from typing import Mapping, Optional


def fetch_file_source_blob_lookup_row(
    sql: str,
    params: tuple,
) -> Optional[Mapping[str, object]]:
    """Fetch the file-source blob metadata/data row through the DB boundary."""
    from app.db import pg_connection

    return pg_connection.fetchone(sql, params)
