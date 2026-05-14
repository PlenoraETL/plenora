"""DB access for admin system status probes."""

from __future__ import annotations

from typing import Any, Sequence


def fetch_latest_verify_run() -> dict[str, Any] | None:
    from app.db.pg_connection import fetchone

    return fetchone(
        "SELECT started_at, finished_at, exit_code, result, error_message "
        "FROM verify_runs ORDER BY started_at DESC LIMIT 1"
    )


def fetch_schedule_rows() -> list[dict[str, Any]]:
    from app.db.pg_connection import fetchall

    return fetchall("SELECT id, name, is_enabled, last_status FROM schedules")


def fetch_db_pool_row() -> dict[str, Any] | None:
    from app.db.pg_connection import fetchone

    return fetchone(
        "SELECT "
        "  (SELECT setting::int FROM pg_settings WHERE name='max_connections') AS max_conn, "
        "  COUNT(*) AS total, "
        "  COUNT(*) FILTER (WHERE state='active') AS active, "
        "  COUNT(*) FILTER (WHERE state='idle') AS idle, "
        "  COUNT(*) FILTER (WHERE state='idle in transaction') AS idle_in_tx, "
        "  COUNT(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting "
        "FROM pg_stat_activity"
    )


def fetch_schema_size_row() -> dict[str, Any] | None:
    from app.db.pg_connection import fetchone

    return fetchone(
        "SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) AS size_pretty, "
        "       SUM(pg_total_relation_size(c.oid)) AS size_bytes "
        "FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'plenora' AND c.relkind = 'r'"
    )


def fetch_critical_table_rows(table_names: Sequence[str]) -> list[dict[str, Any]]:
    from app.db.pg_connection import fetchall

    return fetchall(
        "SELECT c.relname AS table, s.n_live_tup AS rows, "
        "       pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty, "
        "       pg_total_relation_size(c.oid) AS size_bytes "
        "FROM pg_stat_user_tables s "
        "JOIN pg_class c ON c.oid = s.relid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'plenora' AND c.relname = ANY(%s) "
        "ORDER BY size_bytes DESC",
        (list(table_names),),
    )
