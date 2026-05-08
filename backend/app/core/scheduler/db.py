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

from app.db.pg_connection import execute, fetchall, fetchone
from app.db.sql_safety import safe_set_clause


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


def _schedule_legacy_payload(row: ScheduleRow):
    return {
        "id": row.id,
        "group_id": row.group_id,
        "project_id": row.project_id,
        "name": row.name,
        "cron_expression": row.cron_expression,
        "is_enabled": row.is_enabled,
        "config": row.config,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "created_by": row.created_by,
    }


def _schedule_rows(rows: Iterable[Mapping[str, object]]) -> list[ScheduleRow]:
    return [ScheduleRow.from_mapping(row) for row in rows]


def get_schedule_row(schedule_id: str) -> Optional[ScheduleRow]:
    row = fetchone("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
    if row:
        _decode_schedule_config(row)
    return ScheduleRow.from_mapping(row) if row else None


def list_schedule_rows(group_id: str) -> list[ScheduleRow]:
    rows = fetchall(
        "SELECT * FROM schedules WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,),
    )
    for row in rows:
        _decode_schedule_config(row)
    return _schedule_rows(rows)


def get_schedule(schedule_id: str) -> Optional[dict[str, object]]:
    row = get_schedule_row(schedule_id)
    return _schedule_legacy_payload(row) if row else None


def list_schedules(group_id: str) -> list[dict[str, object]]:
    return [_schedule_legacy_payload(row) for row in list_schedule_rows(group_id)]


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
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
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
    execute(f"UPDATE schedules SET {set_clause} WHERE id = ?", vals + (schedule_id,))
    return get_schedule_row(schedule_id)


def delete_schedule(schedule_id: str) -> bool:
    cur = execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return cur.rowcount > 0
