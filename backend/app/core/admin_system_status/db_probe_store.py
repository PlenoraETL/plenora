"""DB access for admin system status probes."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence, TypedDict, cast


class VerifyRunRow(TypedDict, total=False):
    started_at: datetime | str | None
    finished_at: datetime | None
    exit_code: int | None
    result: str | None
    error_message: str | None


class ScheduleProbeRow(TypedDict, total=False):
    id: str
    name: str
    is_enabled: bool
    last_status: str | None


class DbPoolProbeRow(TypedDict, total=False):
    max_conn: int | None
    total: int
    active: int
    idle: int
    idle_in_tx: int
    waiting: int


class SchemaSizeProbeRow(TypedDict, total=False):
    size_pretty: str | None
    size_bytes: int | None


class CriticalTableProbeRow(TypedDict, total=False):
    table: str
    rows: int
    size_pretty: str
    size_bytes: int


def fetch_latest_verify_run() -> VerifyRunRow | None:
    from app.db.pg_connection import fetchone

    return cast(
        VerifyRunRow | None,
        fetchone(
            "SELECT started_at, finished_at, exit_code, result, error_message "
            "FROM verify_runs ORDER BY started_at DESC LIMIT 1"
        ),
    )


def fetch_schedule_rows() -> list[ScheduleProbeRow]:
    from app.db.pg_connection import fetchall

    return cast(
        list[ScheduleProbeRow],
        fetchall("SELECT id, name, is_enabled, last_status FROM schedules"),
    )


def fetch_db_pool_row() -> DbPoolProbeRow | None:
    from app.db.pg_connection import fetchone

    return cast(
        DbPoolProbeRow | None,
        fetchone(
            "SELECT "
            "  (SELECT setting::int FROM pg_settings WHERE name='max_connections') AS max_conn, "
            "  COUNT(*) AS total, "
            "  COUNT(*) FILTER (WHERE state='active') AS active, "
            "  COUNT(*) FILTER (WHERE state='idle') AS idle, "
            "  COUNT(*) FILTER (WHERE state='idle in transaction') AS idle_in_tx, "
            "  COUNT(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting "
            "FROM pg_stat_activity"
        ),
    )


def fetch_schema_size_row() -> SchemaSizeProbeRow | None:
    from app.db.pg_connection import fetchone

    return cast(
        SchemaSizeProbeRow | None,
        fetchone(
            "SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) AS size_pretty, "
            "       SUM(pg_total_relation_size(c.oid)) AS size_bytes "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'plenora' AND c.relkind = 'r'"
        ),
    )


def fetch_critical_table_rows(
    table_names: Sequence[str],
) -> list[CriticalTableProbeRow]:
    from app.db.pg_connection import fetchall

    return cast(
        list[CriticalTableProbeRow],
        fetchall(
            "SELECT c.relname AS table, s.n_live_tup AS rows, "
            "       pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty, "
            "       pg_total_relation_size(c.oid) AS size_bytes "
            "FROM pg_stat_user_tables s "
            "JOIN pg_class c ON c.oid = s.relid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'plenora' AND c.relname = ANY(%s) "
            "ORDER BY size_bytes DESC",
            (list(table_names),),
        ),
    )
