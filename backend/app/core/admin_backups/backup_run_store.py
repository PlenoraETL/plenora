"""DB access for admin backup run metadata."""

from __future__ import annotations

from typing import Any


def fetch_latest_backup_run() -> dict[str, Any] | None:
    from app.db.pg_connection import fetchone

    return fetchone(
        "SELECT id, started_at, finished_at, exit_code, result, bucket_key, "
        "size_bytes, log_tail, error_message "
        "FROM backup_runs ORDER BY started_at DESC LIMIT 1"
    )


def fetch_latest_backup_started_at() -> dict[str, Any] | None:
    from app.db.pg_connection import fetchone

    return fetchone(
        "SELECT started_at FROM backup_runs "
        "ORDER BY started_at DESC LIMIT 1"
    )
