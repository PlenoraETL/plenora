"""DB boundary for CARICA distributed target locks."""

from __future__ import annotations

from datetime import datetime

from app.db.pg_connection import unit_of_work


def acquire_target_lock_row(
    *,
    table_name: str,
    lock_key: str,
    group_id: str,
    target_kind: str,
    target_identity: str,
    owner_token: str,
    acquired_at: datetime,
    expires_at: datetime,
) -> bool:
    with unit_of_work() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {table_name} (
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
                        WHEN {table_name}.owner_token = EXCLUDED.owner_token
                        THEN {table_name}.acquired_at
                        ELSE EXCLUDED.acquired_at
                    END,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
                WHERE {table_name}.expires_at <= %s
                   OR {table_name}.owner_token = EXCLUDED.owner_token
                RETURNING owner_token
                """,
                (
                    lock_key,
                    group_id,
                    target_kind,
                    target_identity,
                    owner_token,
                    acquired_at,
                    expires_at,
                    acquired_at,
                    acquired_at,
                ),
            )
            row = cur.fetchone()
    return row is not None


def release_target_lock_row(
    *,
    table_name: str,
    lock_key: str,
    owner_token: str,
) -> bool:
    with unit_of_work() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM {table_name}
                WHERE lock_key = %s
                  AND owner_token = %s
                """,
                (lock_key, owner_token),
            )
            rowcount = cur.rowcount
    return rowcount > 0


def refresh_target_lock_row(
    *,
    table_name: str,
    lock_key: str,
    owner_token: str,
    refreshed_at: datetime,
    expires_at: datetime,
) -> bool:
    """Extend an unexpired lock only while the same owner still holds it."""
    with unit_of_work() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table_name}
                SET expires_at = %s,
                    updated_at = %s
                WHERE lock_key = %s
                  AND owner_token = %s
                  AND expires_at > %s
                """,
                (
                    expires_at,
                    refreshed_at,
                    lock_key,
                    owner_token,
                    refreshed_at,
                ),
            )
            rowcount = cur.rowcount
    return rowcount > 0
