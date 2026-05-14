"""
Scheduler-owned database helpers for schedules.

Classification: DONE. Schedule SQL and row hydration belong to the
ORCHESTRATION_ENGINE scheduler domain, not to app.db.config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, cast

from app.db import pg_connection as pg
from app.db.sql_safety import safe_set_clause


def execute(sql: str, args: tuple | None = None):
    return pg.execute(sql, args)


def fetchone(sql: str, args: tuple | None = None):
    return pg.fetchone(sql, args)


def fetchall(sql: str, args: tuple | None = None):
    return pg.fetchall(sql, args)


def get_connection():
    return pg.get_connection()


def put_connection(conn) -> None:
    pg.put_connection(conn)


def close_connection() -> None:
    pg.close_connection()


@dataclass(frozen=True)
class ScheduleRow:
    """Read model for the schedules table row shape."""

    id: str
    group_id: str
    project_id: str
    name: str
    cron_expression: str
    is_enabled: bool
    config: object
    created_at: Optional[datetime | str]
    updated_at: Optional[datetime | str]
    created_by: Optional[str]

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "ScheduleRow":
        return cls(
            id=cast(str, row["id"]),
            group_id=cast(str, row["group_id"]),
            project_id=cast(str, row["project_id"]),
            name=cast(str, row["name"]),
            cron_expression=cast(str, row["cron_expression"]),
            is_enabled=bool(row["is_enabled"]),
            config=row.get("config"),
            created_at=cast(Optional[datetime | str], row.get("created_at")),
            updated_at=cast(Optional[datetime | str], row.get("updated_at")),
            created_by=cast(Optional[str], row.get("created_by")),
        )


def _decode_schedule_config(row: dict[str, object]) -> None:
    if isinstance(row.get("config"), str):
        row["config"] = json.loads(cast(str, row["config"]))


def _schedule_rows(rows: Iterable[Mapping[str, object]]) -> list[ScheduleRow]:
    return [ScheduleRow.from_mapping(row) for row in rows]


def get_schedule_row(schedule_id: str) -> Optional[ScheduleRow]:
    row = fetchone("SELECT * FROM schedules WHERE id = %s", (schedule_id,))
    if row:
        _decode_schedule_config(row)
    return ScheduleRow.from_mapping(row) if row else None


def list_schedule_rows(group_id: str) -> list[ScheduleRow]:
    rows = fetchall(
        "SELECT * FROM schedules WHERE group_id = %s ORDER BY created_at DESC",
        (group_id,),
    )
    for row in rows:
        _decode_schedule_config(row)
    return _schedule_rows(rows)


def save_schedule(
    schedule_id: str,
    group_id: str,
    project_id: str,
    name: str,
    cron_expression: str,
    is_enabled: bool = True,
    config: Optional[dict[str, object]] = None,
    created_by: Optional[str] = None,
) -> ScheduleRow:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO schedules (id, group_id, project_id, name, cron_expression, is_enabled, config, created_at, updated_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
        "name=EXCLUDED.name, cron_expression=EXCLUDED.cron_expression, is_enabled=EXCLUDED.is_enabled, "
        "config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (
            schedule_id,
            group_id,
            project_id,
            name,
            cron_expression,
            is_enabled,
            json.dumps(config or {}),
            now,
            now,
            created_by,
        ),
    )
    row = get_schedule_row(schedule_id)
    if row is None:
        raise RuntimeError(f"Schedule '{schedule_id}' was not persisted")
    return row


def update_schedule(
    schedule_id: str,
    **fields: object,
) -> Optional[ScheduleRow]:
    if "config" in fields and isinstance(fields["config"], dict):
        fields["config"] = json.dumps(fields["config"])
    if "is_enabled" in fields:
        fields["is_enabled"] = bool(fields["is_enabled"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = safe_set_clause("schedules", fields)
    execute(f"UPDATE schedules SET {set_clause} WHERE id = %s", vals + (schedule_id,))
    return get_schedule_row(schedule_id)


def delete_schedule(schedule_id: str) -> bool:
    cur = execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
    return cur.rowcount > 0


def get_beat_heartbeat():
    return fetchone("SELECT ts FROM beat_heartbeat WHERE id = 'beat'")


def count_enabled_schedules() -> int:
    row = fetchone("SELECT COUNT(*) as n FROM schedules WHERE is_enabled = TRUE")
    return int(row["n"]) if row else 0


def count_stuck_schedules(hard_limit_seconds: int) -> int:
    row = fetchone(
        "SELECT COUNT(*) as n FROM schedules "
        "WHERE last_status = 'running' "
        "AND last_run IS NOT NULL "
        "AND last_run < NOW() - (%s * INTERVAL '1 second')",
        (hard_limit_seconds,),
    )
    return int(row["n"]) if row else 0


def list_project_ids() -> set[str]:
    return {cast(str, row["id"]) for row in fetchall("SELECT id FROM projects")}


def insert_schedule_log(
    *,
    log_id: str,
    schedule_id: str,
    timestamp: datetime,
    level: str,
    event: str,
    triggered_by: str,
    steps_json: str,
    total_duration_ms: int,
    error_message: Optional[str],
    schedule_info_json: Optional[str],
    pipeline_info_json: Optional[str],
    execution_info_json: Optional[str],
) -> None:
    execute(
        "INSERT INTO schedule_logs "
        "(log_id, schedule_id, timestamp, level, event, triggered_by, "
        " steps, total_duration_ms, error_message, schedule_info, "
        " pipeline_info, execution_info) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)",
        (
            log_id,
            schedule_id,
            timestamp,
            level,
            event,
            triggered_by,
            steps_json,
            total_duration_ms,
            error_message,
            schedule_info_json,
            pipeline_info_json,
            execution_info_json,
        ),
    )


def get_schedule_log_row(log_id: str) -> Optional[dict]:
    return fetchone("SELECT * FROM schedule_logs WHERE log_id = %s", (log_id,))


def count_schedule_log_rows(
    schedule_id: str,
    event_filter: Optional[str] = None,
) -> int:
    if event_filter:
        row = fetchone(
            "SELECT COUNT(*) AS n FROM schedule_logs "
            "WHERE schedule_id = %s AND event = %s",
            (schedule_id, event_filter),
        )
    else:
        row = fetchone(
            "SELECT COUNT(*) AS n FROM schedule_logs WHERE schedule_id = %s",
            (schedule_id,),
        )
    return int(row["n"]) if row else 0


def list_schedule_log_rows(
    schedule_id: str,
    *,
    page_size: int,
    offset: int,
    event_filter: Optional[str] = None,
) -> list[dict]:
    if event_filter:
        return fetchall(
            "SELECT * FROM schedule_logs "
            "WHERE schedule_id = %s AND event = %s "
            "ORDER BY timestamp DESC LIMIT %s OFFSET %s",
            (schedule_id, event_filter, page_size, offset),
        )
    return fetchall(
        "SELECT * FROM schedule_logs "
        "WHERE schedule_id = %s "
        "ORDER BY timestamp DESC LIMIT %s OFFSET %s",
        (schedule_id, page_size, offset),
    )


def get_latest_schedule_log_row(schedule_id: str) -> Optional[dict]:
    return fetchone(
        "SELECT * FROM schedule_logs WHERE schedule_id = %s "
        "ORDER BY timestamp DESC LIMIT 1",
        (schedule_id,),
    )


def delete_schedule_logs_before(before_dt: datetime) -> int:
    cur = execute("DELETE FROM schedule_logs WHERE timestamp < %s", (before_dt,))
    return int(cur.rowcount)


def delete_schedule_logs_for_schedule(schedule_id: str) -> int:
    cur = execute("DELETE FROM schedule_logs WHERE schedule_id = %s", (schedule_id,))
    return int(cur.rowcount)


def list_schedule_logs(
    *,
    schedule_id: str,
    events: Optional[Iterable[str]] = None,
    limit: int = 200,
) -> list[dict]:
    """Lista entries di `schedule_logs` per uno schedule.

    Usato da `ScheduleRunsCatalog.list_runs` per popolare lo storico
    runs anche quando il filesystem non ha cartelle archiviate (post
    refactor blob→DB i risultati vivono in `results`, non come file).

    Args:
        schedule_id: filtro obbligatorio.
        events: tupla di event-type da includere (es. PIPELINE_COMPLETED).
            Se None, tutti gli event.
        limit: cap conservativo per evitare scan illimitati su schedule
            molto attivi.

    Returns:
        Lista di dict con almeno `timestamp`, `event`, `level`,
        `total_duration_ms`, `pipeline_info`, `execution_info`. Il
        chiamante puo' costruire il proprio shape di output.
    """
    if events:
        placeholders = ",".join(["%s"] * len(list(events)))
        rows = fetchall(
            f"SELECT log_id, schedule_id, timestamp, level, event, "
            f"triggered_by, total_duration_ms, error_message, "
            f"pipeline_info, execution_info "
            f"FROM schedule_logs "
            f"WHERE schedule_id = %s AND event IN ({placeholders}) "
            f"ORDER BY timestamp DESC LIMIT %s",
            (schedule_id, *events, limit),
        )
    else:
        rows = fetchall(
            "SELECT log_id, schedule_id, timestamp, level, event, "
            "triggered_by, total_duration_ms, error_message, "
            "pipeline_info, execution_info "
            "FROM schedule_logs WHERE schedule_id = %s "
            "ORDER BY timestamp DESC LIMIT %s",
            (schedule_id, limit),
        )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # Convenienza per i caller filesystem-style: stringa
        # YYYYMMDD_HHMMSS dal timestamp DB.
        ts = d.get("timestamp")
        if isinstance(ts, datetime):
            d["timestamp_str"] = ts.strftime("%Y%m%d_%H%M%S")
        out.append(d)
    return out
