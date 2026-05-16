"""Domain-owned database access for datasource persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional, TypeAlias, cast

from app.db.pg_connection import unit_of_work
from app.db.sql_safety import safe_set_clause


DataSourceConfig: TypeAlias = Mapping[str, object]
ScanStatePayload: TypeAlias = Mapping[str, object]


def _fetchone(sql: str, params: tuple = ()) -> Mapping[str, object] | None:
    with unit_of_work() as session:
        return session.fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, object]]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    with unit_of_work() as session:
        return session.execute(sql, params)


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


def _config_from_raw(value: object) -> DataSourceConfig:
    if value is None:
        return {}
    parsed = value
    if isinstance(value, str):
        parsed = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return dict(cast(Mapping[str, object], parsed))


def _optional_mapping_from_raw(
    value: object,
    *,
    field_name: str,
) -> Mapping[str, object] | None:
    if value is None:
        return None
    parsed = value
    if isinstance(value, str):
        parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Malformed datasource DB row: {field_name} must be a JSON object or null"
        )
    return dict(cast(Mapping[str, object], parsed))


@dataclass(frozen=True)
class DataSourceRow:
    """Typed database datasource row consumed inside the datasources domain."""

    id: str
    group_id: str
    name: str
    ds_type: str
    config: DataSourceConfig
    created_at: object | None = None
    updated_at: object | None = None
    created_by: str | None = None
    scan_state: ScanStatePayload | None = None

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
            scan_state=_optional_mapping_from_raw(
                row.get("scan_state"),
                field_name="scan_state",
            ),
        )


@dataclass(frozen=True)
class DataSourceScanStateRow:
    """Datasource scan-state projection for scheduler runtime reads."""

    id: str
    scan_state: ScanStatePayload | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "DataSourceScanStateRow":
        return cls(
            id=_required_str(row, "id"),
            scan_state=_optional_mapping_from_raw(
                row.get("scan_state"),
                field_name="scan_state",
            ),
        )


def _optional_datasource_row(row: Mapping[str, object] | None) -> Optional[DataSourceRow]:
    return DataSourceRow.from_mapping(row) if row else None


def get_datasource(
    ds_id: str,
    group_id: str | None = None,
) -> Optional[DataSourceRow]:
    if group_id:
        row = _fetchone(
            "SELECT * FROM datasources WHERE id = %s AND group_id = %s",
            (ds_id, group_id),
        )
    else:
        row = _fetchone("SELECT * FROM datasources WHERE id = %s", (ds_id,))
    return _optional_datasource_row(row)


def list_datasources(group_id: str | None = None) -> list[DataSourceRow]:
    if group_id is None:
        rows = _fetchall("SELECT * FROM datasources ORDER BY name")
    else:
        rows = _fetchall(
            "SELECT * FROM datasources WHERE group_id = %s ORDER BY name",
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
    _execute(
        "INSERT INTO datasources (id, group_id, name, ds_type, config, created_at, updated_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
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
    _execute(f"UPDATE datasources SET {set_clause} WHERE id = %s", params + (ds_id,))


def delete_datasource(ds_id: str) -> bool:
    cur = _execute("DELETE FROM datasources WHERE id = %s", (ds_id,))
    return cur.rowcount > 0


def get_scan_state(ds_id: str) -> Optional[DataSourceScanStateRow]:
    row = _fetchone(
        "SELECT id, scan_state FROM datasources WHERE id = %s",
        (ds_id,),
    )
    return DataSourceScanStateRow.from_mapping(row) if row else None


def save_scan_state(ds_id: str, state: Mapping[str, object]) -> bool:
    cur = _execute(
        "UPDATE datasources SET scan_state = %s, updated_at = %s WHERE id = %s",
        (
            json.dumps(dict(state)),
            datetime.now(timezone.utc).isoformat(),
            ds_id,
        ),
    )
    return cur.rowcount > 0
