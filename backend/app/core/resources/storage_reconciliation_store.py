"""Leaf DB store for storage reconciliation queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import app.db.pg_connection as pg_connection


def execute_reconciliation_update(sql: str, params: tuple[Any, ...]):
    return pg_connection.execute(sql, params)


def fetch_due_pending_delete_rows(
    sql: str,
    *,
    cutoff: datetime,
    limit: int,
):
    return pg_connection.fetchall(sql, (cutoff, limit))


def fetch_active_state_domain_count_rows(sql: str):
    return pg_connection.fetchall(sql, ())


def delete_terminal_rows(
    sql: str,
    *,
    older_than: datetime,
    limit: int,
):
    return pg_connection.execute(sql, (older_than, limit))


def insert_upload_intent_row(
    sql: str,
    *,
    domain: str,
    storage_key: str,
    reason: str,
    extras_json: str | None,
):
    return pg_connection.fetchone(sql, (domain, storage_key, reason, extras_json))


def fetch_due_stale_upload_intent_rows(
    sql: str,
    *,
    grace_cutoff: datetime,
    now: datetime,
    limit: int,
):
    return pg_connection.fetchall(sql, (grace_cutoff, now, limit))


def fetch_storage_key_reference_rows(
    sql: str,
    *,
    storage_key: str,
):
    return pg_connection.fetchall(sql, (storage_key,))


def fetch_pending_operation_count_rows(sql: str):
    return pg_connection.fetchall(sql, ())
