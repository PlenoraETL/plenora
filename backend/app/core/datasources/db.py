"""Domain-owned database access for datasource persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional, cast

from app.db.pg_connection import execute, fetchall, fetchone
from app.db.sql_safety import safe_set_clause


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Malformed datasource DB row: {key} must be a string")
    return value


def _optional_str(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"Malformed datasource DB row: {field_name} must be a string or null"
        )
    return value


def _config_from_raw(value: object) -> dict[str, object]:
    if value is None:
        return {}
    parsed = value
    if isinstance(value, str):
        parsed = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return dict(cast(Mapping[str, object], parsed))


@dataclass(frozen=True)
class DataSourceRow:
    """Typed database datasource row consumed inside the datasources domain."""

    id: str
    group_id: str
    name: str
    ds_type: str
    config: dict[str, object]
    created_at: object | None = None
    updated_at: object | None = None
    created_by: str | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "DataSourceRow":
        return cls(
            id=_required_str(row, "id"),
            group_id=_required_str(row, "group_id"),
            name=_required_str(row, "name"),
            ds_type=_required_str(row, "ds_type"),
            config=_config_from_raw(row.get("config")),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            created_by=_optional_str(row.get("created_by"), field_name="created_by"),
        )

    def to_legacy_dict(self) -> dict[str, object]:
        """KEEP_PUBLIC_COMPAT: dict shape for existing service internals/tests."""
        return {
            "id": self.id,
            "group_id": self.group_id,
            "name": self.name,
            "ds_type": self.ds_type,
            "config": dict(self.config),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
        }


def _optional_datasource_row(row: Mapping[str, object] | None) -> Optional[DataSourceRow]:
    return DataSourceRow.from_mapping(row) if row else None


def get_datasource(
    ds_id: str,
    group_id: str | None = None,
) -> Optional[DataSourceRow]:
    if group_id:
        row = fetchone(
            "SELECT * FROM datasources WHERE id = ? AND group_id = ?",
            (ds_id, group_id),
        )
    else:
        row = fetchone("SELECT * FROM datasources WHERE id = ?", (ds_id,))
    return _optional_datasource_row(row)


def list_datasources(group_id: str | None = None) -> list[DataSourceRow]:
    if group_id is None:
        rows = fetchall("SELECT * FROM datasources ORDER BY name")
    else:
        rows = fetchall(
            "SELECT * FROM datasources WHERE group_id = ? ORDER BY name",
            (group_id,),
        )
    return [DataSourceRow.from_mapping(row) for row in rows]


def save_datasource(
    ds_id: str,
    group_id: str,
    name: str,
    ds_type: str,
    config: Mapping[str, object],
    created_by: str | None = None,
) -> DataSourceRow:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO datasources (id, group_id, name, ds_type, config, created_at, updated_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET "
        "name=EXCLUDED.name, ds_type=EXCLUDED.ds_type, config=EXCLUDED.config, updated_at=EXCLUDED.updated_at",
        (
            ds_id,
            group_id,
            name,
            ds_type,
            json.dumps(dict(config)),
            now,
            now,
            created_by,
        ),
    )
    row = get_datasource(ds_id)
    if row is None:
        raise RuntimeError(f"datasource '{ds_id}' was saved but could not be loaded")
    return row


def update_datasource(ds_id: str, **fields: object) -> None:
    if not fields:
        return
    update_fields = dict(fields)
    if "config" in update_fields and isinstance(update_fields["config"], Mapping):
        update_fields["config"] = json.dumps(dict(update_fields["config"]))
    update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, params = safe_set_clause("datasources", update_fields)
    execute(f"UPDATE datasources SET {set_clause} WHERE id = ?", params + (ds_id,))


def delete_datasource(ds_id: str) -> bool:
    cur = execute("DELETE FROM datasources WHERE id = ?", (ds_id,))
    return cur.rowcount > 0
