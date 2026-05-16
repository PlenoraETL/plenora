"""Distributed target locks for CARICA destination writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.db import pg_connection as db
from app.utils.logger import get_logger


logger = get_logger(__name__)

_TABLE_NAME = "carica_target_locks"
_LOCK_VERSION = "v1"
_DEFAULT_DB_SCHEMA = "public"


@dataclass(frozen=True)
class CaricaTargetLock:
    """Resolved write target protected by a distributed PostgreSQL lock."""

    key: str
    kind: str
    identity: str
    group_id: str


@dataclass(frozen=True)
class CaricaTargetLockHandle:
    """Owned lock row that can be released only by its owner token."""

    target: CaricaTargetLock
    owner_token: str


def resolve_carica_target_lock(
    *,
    node_data: Mapping[str, object],
    project_id: str,
    group_id: str,
    write_branch: str,
) -> CaricaTargetLock | None:
    """Return a tenant-scoped lock target, or None for legacy unknown shapes."""
    tenant_scope = _tenant_scope(project_id=project_id, group_id=group_id)
    target: Mapping[str, object] | None

    if write_branch == "db":
        target = _db_target(node_data)
    elif write_branch == "rest":
        target = _rest_target(node_data)
    elif write_branch == "arcgis":
        target = _arcgis_target(node_data)
    else:
        return None

    if target is None:
        return None

    identity_payload = {
        "version": _LOCK_VERSION,
        "tenant": tenant_scope,
        "target": target,
    }
    identity = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    kind = str(target["kind"])
    return CaricaTargetLock(
        key=f"carica:{kind}:{digest}",
        kind=kind,
        identity=identity,
        group_id=group_id or "",
    )


def acquire_carica_target_lock(
    *,
    target: CaricaTargetLock,
    owner_token: str,
    ttl_seconds: int,
) -> CaricaTargetLockHandle | None:
    """Try to acquire target lock without waiting.

    Returns a handle on success. Returns None when another unexpired owner
    already holds the target lock. Database errors propagate so callers fail
    closed instead of writing without a distributed lock.
    """
    ttl = max(1, int(ttl_seconds))
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_TABLE_NAME} (
                    lock_key,
                    group_id,
                    target_kind,
                    target_identity,
                    owner_token,
                    acquired_at,
                    expires_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (lock_key) DO UPDATE SET
                    group_id = EXCLUDED.group_id,
                    target_kind = EXCLUDED.target_kind,
                    target_identity = EXCLUDED.target_identity,
                    owner_token = EXCLUDED.owner_token,
                    acquired_at = CASE
                        WHEN {_TABLE_NAME}.owner_token = EXCLUDED.owner_token
                        THEN {_TABLE_NAME}.acquired_at
                        ELSE EXCLUDED.acquired_at
                    END,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
                WHERE {_TABLE_NAME}.expires_at <= %s
                   OR {_TABLE_NAME}.owner_token = EXCLUDED.owner_token
                RETURNING owner_token
                """,
                (
                    target.key,
                    target.group_id,
                    target.kind,
                    target.identity,
                    owner_token,
                    now,
                    expires_at,
                    now,
                    now,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_connection(conn)

    if not row:
        return None
    return CaricaTargetLockHandle(target=target, owner_token=owner_token)


def release_carica_target_lock(handle: CaricaTargetLockHandle) -> bool:
    """Release a lock only if the caller still owns the row."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {_TABLE_NAME}
                WHERE lock_key = %s
                  AND owner_token = %s
                """,
                (handle.target.key, handle.owner_token),
            )
            rowcount = cur.rowcount
        conn.commit()
        return rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_connection(conn)


def _tenant_scope(*, project_id: str, group_id: str) -> Mapping[str, str]:
    if group_id:
        return {"group_id": group_id}
    return {"project_id": project_id}


def _db_target(node_data: Mapping[str, object]) -> Mapping[str, object] | None:
    connection_id = _non_empty_string(node_data.get("connection_id"))
    table_name = _non_empty_string(node_data.get("table_name"))
    if not connection_id or not table_name:
        return None
    schema_name = _non_empty_string(node_data.get("schema_name")) or _DEFAULT_DB_SCHEMA
    return {
        "kind": "db",
        "connection_id": connection_id,
        "schema_name": schema_name,
        "table_name": table_name,
    }


def _rest_target(node_data: Mapping[str, object]) -> Mapping[str, object] | None:
    rest_data = _nested_mapping(node_data, "rest_config") or _nested_mapping(node_data, "rest")
    source = rest_data if rest_data is not None else node_data
    url = _non_empty_string(
        _first_value(source, ("url", "rest_url", "endpoint_url"))
    )
    if not url:
        return None
    normalized_url = _normalize_url(url)
    if not normalized_url:
        return None
    method = (
        _non_empty_string(_first_value(source, ("method", "rest_method")))
        or "POST"
    ).upper()
    return {
        "kind": "rest",
        "method": method,
        "url": normalized_url,
    }


def _arcgis_target(node_data: Mapping[str, object]) -> Mapping[str, object] | None:
    connection_id = _non_empty_string(node_data.get("arcgis_connection_id"))
    operation = _non_empty_string(node_data.get("arcgis_operation")) or "add"
    if not connection_id:
        return None

    if operation == "create_service":
        service_name = _non_empty_string(node_data.get("arcgis_new_service_name"))
        if not service_name:
            return None
        return {
            "kind": "arcgis",
            "connection_id": connection_id,
            "operation": operation,
            "folder_id": _non_empty_string(node_data.get("arcgis_folder_id")) or "",
            "service_name": service_name,
        }

    layer_url = _non_empty_string(node_data.get("arcgis_layer_url"))
    normalized_url = _normalize_url(layer_url) if layer_url else ""
    if not normalized_url:
        return None
    return {
        "kind": "arcgis",
        "connection_id": connection_id,
        "layer_url": normalized_url,
    }


def _nested_mapping(
    data: Mapping[str, object],
    key: str,
) -> Mapping[str, object] | None:
    value = data.get(key)
    return value if isinstance(value, Mapping) else None


def _first_value(data: Mapping[str, object], names: tuple[str, ...]) -> object | None:
    for name in names:
        if name in data:
            return data[name]
    return None


def _non_empty_string(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            "",
            query,
            "",
        )
    )
