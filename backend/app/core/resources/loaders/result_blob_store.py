"""DB lookup boundary for the result-blob resource loader."""

from __future__ import annotations

from typing import Mapping, Optional

from app.db import pg_connection


def fetch_result_blob_lookup_row(
    sql: str,
    params: tuple,
) -> Optional[Mapping[str, object]]:
    """Fetch the result blob metadata/data row through the DB boundary."""
    return pg_connection.fetchone(sql, params)
