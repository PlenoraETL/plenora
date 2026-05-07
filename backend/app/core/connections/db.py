"""Domain-owned database bridge for connection persistence.

The SQL implementation remains in the existing config DB leaf while callers
move to the connection domain boundary.
"""

from __future__ import annotations

from typing import Optional

from app.db.config.connections import create_connection as _create_connection
from app.db.config.connections import delete_connection as _delete_connection
from app.db.config.connections import get_connection_config as _get_connection_config
from app.db.config.connections import update_connection as _update_connection
from app.db.pg_connection import fetchall

ConnectionRow = dict[str, object]


def get_connection_config(conn_id: str, group_id: str | None = None) -> Optional[ConnectionRow]:
    return _get_connection_config(conn_id, group_id)


def create_connection(
    conn_id: str,
    group_id: str,
    name: str,
    db_type: str,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    db_schema: str | None = None,
    username: str | None = None,
    password: str | None = None,
    extra: ConnectionRow | None = None,
    created_by: str | None = None,
) -> ConnectionRow:
    return _create_connection(
        conn_id=conn_id,
        group_id=group_id,
        name=name,
        db_type=db_type,
        host=host,
        port=port,
        database=database,
        db_schema=db_schema,
        username=username,
        password=password,
        extra=extra,
        created_by=created_by,
    )


def update_connection(conn_id: str, **fields: object) -> Optional[ConnectionRow]:
    return _update_connection(conn_id, **fields)


def delete_connection(conn_id: str) -> bool:
    return _delete_connection(conn_id)


def list_connection_configs(group_id: str | None = None) -> list[ConnectionRow]:
    if group_id is None:
        return fetchall("SELECT * FROM connections ORDER BY name")
    return fetchall(
        "SELECT * FROM connections WHERE group_id = %s ORDER BY name",
        (group_id,),
    )
