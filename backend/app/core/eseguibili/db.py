"""
CRUD per la tabella `eseguibili` (storage: PostgreSQL via

`app.db.pg_connection`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

from app.db.pg_connection import unit_of_work
from app.db.sql_safety import safe_set_clause
from app.shared.contracts.payloads import JsonObject, JsonRows


ExpectedColumns = JsonRows


class _DbFacade:
    def fetchone(self, sql: str, params: tuple = ()) -> JsonObject | None:
        with unit_of_work() as session:
            return session.fetchone(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> JsonRows:
        with unit_of_work() as session:
            return session.fetchall(sql, params)

    def execute(self, sql: str, params: tuple = ()):
        with unit_of_work() as session:
            return session.execute(sql, params)


db = _DbFacade()


def _fetchone(sql: str, params: tuple = ()) -> JsonObject | None:
    return db.fetchone(sql, params)


def _fetchall(sql: str, params: tuple = ()) -> JsonRows:
    return db.fetchall(sql, params)


def _execute(sql: str, params: tuple = ()):
    return db.execute(sql, params)


@dataclass(frozen=True)
class EseguibileRow:
    id: str
    name: str
    description: str
    canvas: JsonObject
    expected_columns: ExpectedColumns
    default_output_format: str
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    default_output_name: str | None = None
    icon: str | None = None
    source_canvas_id: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass(frozen=True)
class EseguibileSummaryRow:
    id: str
    name: str
    description: str
    default_output_format: str
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    expected_columns_count: int = 0
    default_output_name: str | None = None
    icon: str | None = None
    source_canvas_id: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass(frozen=True)
class EseguibileSourceRefRow:
    id: str
    name: str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> object:
        return getattr(self, key, default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_str(value: object) -> str | None:
    """Best-effort coercion to ISO string for datetime values returned by psycopg2.

    psycopg2 returns ``TIMESTAMPTZ`` as ``datetime`` objects by default; the
    eseguibile DTO declares those columns as ``str``. Without coercion the
    factory raises ``Malformed eseguibile DB row`` even on legitimate rows.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = _coerce_str(row.get(key))
    if value is None:
        raise ValueError(f"Malformed eseguibile DB row: {key} must be a string")
    return value


def _optional_str(row: Mapping[str, object], key: str) -> str | None:
    raw = row.get(key)
    if raw is None:
        return None
    return _coerce_str(raw)


def _parse_json_object(value: object, *, field_name: str) -> JsonObject:
    if value is None or value == "":
        return {}
    parsed: object
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Malformed eseguibile DB row: {field_name} must decode to an object"
        )
    return parsed


def _parse_expected_columns(value: object) -> ExpectedColumns:
    if value is None or value == "":
        return []
    parsed: object
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError(
            "Malformed eseguibile DB row: expected_columns must decode to a list"
        )
    return [item for item in parsed if isinstance(item, dict)]


def eseguibile_row_from_db(row: Mapping[str, object]) -> EseguibileRow:
    return EseguibileRow(
        id=_required_str(row, "id"),
        name=_required_str(row, "name"),
        description=_optional_str(row, "description") or "",
        canvas=_parse_json_object(row.get("canvas"), field_name="canvas"),
        expected_columns=_parse_expected_columns(row.get("expected_columns")),
        default_output_format=_required_str(row, "default_output_format"),
        default_output_name=_optional_str(row, "default_output_name"),
        icon=_optional_str(row, "icon"),
        source_canvas_id=_optional_str(row, "source_canvas_id"),
        group_id=_required_str(row, "group_id"),
        created_by=_required_str(row, "created_by"),
        created_at=_required_str(row, "created_at"),
        updated_at=_required_str(row, "updated_at"),
    )


def eseguibile_summary_row_from_db(row: Mapping[str, object]) -> EseguibileSummaryRow:
    return EseguibileSummaryRow(
        id=_required_str(row, "id"),
        name=_required_str(row, "name"),
        description=_optional_str(row, "description") or "",
        default_output_format=_required_str(row, "default_output_format"),
        default_output_name=_optional_str(row, "default_output_name"),
        icon=_optional_str(row, "icon"),
        source_canvas_id=_optional_str(row, "source_canvas_id"),
        group_id=_required_str(row, "group_id"),
        created_by=_required_str(row, "created_by"),
        created_at=_required_str(row, "created_at"),
        updated_at=_required_str(row, "updated_at"),
        expected_columns_count=len(_parse_expected_columns(row.get("expected_columns"))),
    )


def eseguibile_source_ref_row_from_db(
    row: Mapping[str, object],
) -> EseguibileSourceRefRow:
    return EseguibileSourceRefRow(
        id=_required_str(row, "id"),
        name=_required_str(row, "name"),
    )


def create_eseguibile(
    name: str,
    canvas: JsonObject,
    expected_columns: ExpectedColumns,
    group_id: str,
    created_by: str,
    description: str = "",
    default_output_format: str = "xlsx",
    default_output_name: Optional[str] = None,
    source_canvas_id: Optional[str] = None,
    icon: Optional[str] = None,
) -> EseguibileRow:
    """Crea un nuovo eseguibile. Ritorna il record creato."""
    eid = f"exec_{uuid.uuid4().hex[:12]}"
    now = _now()
    _execute(
        """
        INSERT INTO eseguibili (
            id, name, description, canvas, expected_columns,
            default_output_format, default_output_name, icon, source_canvas_id,
            group_id, created_by, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            eid, name, description,
            json.dumps(canvas, ensure_ascii=False),
            json.dumps(expected_columns, ensure_ascii=False),
            default_output_format, default_output_name, icon, source_canvas_id,
            group_id, created_by, now, now,
        )
    )
    created = get_eseguibile(eid)
    if created is None:
        raise RuntimeError(f"Eseguibile '{eid}' was inserted but not found")
    return created


def get_eseguibile(eid: str, group_id: str = None) -> Optional[EseguibileRow]:
    """Ritorna l'eseguibile con canvas e expected_columns deserializzati.

    group_id=None → no filter (backward compat per uso interno).
    Passare group_id dal router per defense-in-depth multi-tenant.
    """
    if group_id:
        row = _fetchone(
            "SELECT * FROM eseguibili WHERE id = %s AND group_id = %s",
            (eid, group_id)
        )
    else:
        row = _fetchone("SELECT * FROM eseguibili WHERE id = %s", (eid,))
    return eseguibile_row_from_db(row) if row else None


def list_eseguibili(
    group_id: Optional[str] = None,
    *,
    resource_ids: set[str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[EseguibileSummaryRow]:
    """Lista eseguibili, opzionalmente filtrati per gruppo.

    Non carica canvas/expected_columns per la summary (prestazioni).
    Usa get_eseguibile per i dettagli completi.
    """
    columns = """id, name, description, default_output_format, default_output_name,
                 icon, source_canvas_id, group_id, created_by, created_at, updated_at,
                 expected_columns"""
    if resource_ids is not None:
        if not resource_ids:
            return []
        ordered_ids = sorted(resource_ids)
        placeholders = ", ".join(["%s"] * len(ordered_ids))
        rows = _fetchall(
            f"SELECT {columns} FROM eseguibili "
            f"WHERE id IN ({placeholders}) ORDER BY created_at DESC "
            "LIMIT %s OFFSET %s",
            tuple(ordered_ids) + (limit, offset),
        )
    elif group_id:
        rows = _fetchall(
            f"SELECT {columns} FROM eseguibili WHERE group_id = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (group_id, limit, offset)
        )
    else:
        rows = _fetchall(
            f"SELECT {columns} FROM eseguibili ORDER BY created_at DESC "
            "LIMIT %s OFFSET %s",
            (limit, offset),
        )
    return [eseguibile_summary_row_from_db(row) for row in rows]


def list_eseguibili_by_source(source_canvas_id: str) -> list[EseguibileSourceRefRow]:
    """Ritorna gli eseguibili pubblicati a partire dal progetto indicato.

    Restituisce solo id e name (sufficienti per messaggi di errore).
    """
    rows = _fetchall(
        "SELECT id, name FROM eseguibili WHERE source_canvas_id = %s",
        (source_canvas_id,)
    )
    return [eseguibile_source_ref_row_from_db(row) for row in rows]


def mark_temp_file_source_ttl(file_source_id: str) -> None:
    """Marca un file_source temporaneo creato dal runtime eseguibili."""
    _execute(
        """
        UPDATE file_sources
        SET is_temp = true,
            expires_at = now() + INTERVAL '24 hours'
        WHERE id = %s
        """,
        (file_source_id,),
    )


def update_eseguibile(eid: str, **fields: object) -> Optional[EseguibileRow]:
    """Aggiorna i campi passati (solo metadata, non canvas/expected_columns)."""
    to_update = {
        k: v
        for k, v in fields.items()
        if k in {"name", "description", "default_output_format", "default_output_name", "icon"}
        and v is not None
    }
    if not to_update:
        return get_eseguibile(eid)
    to_update["updated_at"] = _now()
    set_clause, vals = safe_set_clause("eseguibili", to_update)
    _execute(f"UPDATE eseguibili SET {set_clause} WHERE id = %s", vals + (eid,))
    return get_eseguibile(eid)


def delete_eseguibile(eid: str) -> bool:
    """Elimina un eseguibile. Ritorna True se almeno una riga è stata cancellata."""
    # db.execute non restituisce rowcount in modo uniforme, verifichiamo prima
    if get_eseguibile(eid) is None:
        return False
    _execute("DELETE FROM eseguibili WHERE id = %s", (eid,))
    return True
