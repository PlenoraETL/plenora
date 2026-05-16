"""Leaf DB store for storage reconciliation queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.pg_connection import unit_of_work


def execute_reconciliation_update(sql: str, params: tuple[Any, ...]):
    with unit_of_work() as session:
        return session.execute(sql, params)


def fetch_due_pending_delete_rows(
    sql: str,
    *,
    cutoff: datetime,
    limit: int,
):
    with unit_of_work() as session:
        return session.fetchall(sql, (cutoff, limit))


def fetch_active_state_domain_count_rows(sql: str):
    with unit_of_work() as session:
        return session.fetchall(sql, ())


def delete_terminal_rows(
    sql: str,
    *,
    older_than: datetime,
    limit: int,
):
    with unit_of_work() as session:
        return session.execute(sql, (older_than, limit))


def insert_upload_intent_row(
    sql: str,
    *,
    domain: str,
    storage_key: str,
    reason: str,
    extras_json: str | None,
):
    with unit_of_work() as session:
        return session.fetchone(sql, (domain, storage_key, reason, extras_json))


def fetch_due_stale_upload_intent_rows(
    sql: str,
    *,
    grace_cutoff: datetime,
    now: datetime,
    limit: int,
):
    with unit_of_work() as session:
        return session.fetchall(sql, (grace_cutoff, now, limit))


def fetch_storage_key_reference_rows(
    sql: str,
    *,
    storage_key: str,
):
    with unit_of_work() as session:
        return session.fetchall(sql, (storage_key,))


def fetch_pending_operation_count_rows(sql: str):
    with unit_of_work() as session:
        return session.fetchall(sql, ())
