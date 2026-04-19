"""Seed/cleanup di file_source reali per gli integration test."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Iterable

from tests2.integration.harness.db import make_it_id, pg_execute


def _csv_bytes(rows: Iterable[dict]) -> bytes:
    rows = list(rows)
    if not rows:
        rows = [{"value": ""}]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def seed_csv_file_source(
    *,
    group_id: str,
    created_by: str,
    rows: Iterable[dict],
    name: str = "Integration CSV Source",
    original_filename: str = "integration.csv",
) -> dict:
    """Crea un file_source CSV direttamente su Postgres reale."""
    file_id = make_it_id("fs")
    now = datetime.now(timezone.utc).isoformat()
    sample_rows = list(rows)
    payload = _csv_bytes(sample_rows)
    if not sample_rows:
        sample_rows = [{"value": ""}]
    columns = [{"name": key, "type": "str"} for key in sample_rows[0].keys()]
    metadata = {
        "id": file_id,
        "name": name,
        "description": "tests2 integration seeded file_source",
        "original_filename": original_filename,
        "format": "csv",
        "group_id": group_id,
        "layer": None,
        "sheet": None,
        "columns": columns,
        "row_count": len(sample_rows),
        "file_size": len(payload),
        "crs": None,
        "created_at": now,
        "updated_at": now,
        "created_by": created_by,
    }
    pg_execute(
        "INSERT INTO file_sources "
        "(id, group_id, name, original_filename, format, data, size_bytes, metadata, created_at, updated_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)",
        (
            file_id,
            group_id,
            name,
            original_filename,
            "csv",
            payload,
            len(payload),
            json.dumps(metadata),
            now,
            now,
            created_by,
        ),
    )
    return metadata


def delete_file_source(file_id: str) -> None:
    """Cleanup esplicito di un file_source seedato."""
    pg_execute("DELETE FROM file_sources WHERE id = %s", (file_id,))
