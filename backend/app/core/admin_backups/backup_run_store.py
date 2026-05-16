"""DB access for admin backup run metadata."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict, cast


class BackupRunRow(TypedDict, total=False):
    id: str
    started_at: datetime | str
    finished_at: datetime | str | None
    exit_code: int | None
    result: str | None
    bucket_key: str | None
    size_bytes: int | None
    log_tail: str | None
    error_message: str | None


class BackupStartedAtRow(TypedDict):
    started_at: datetime | str


class ManualBackupLockRow(TypedDict):
    lock_name: str
    owner_token: str
    owner_pid: int | None
    acquired_at: datetime | str
    expires_at: datetime | str


MANUAL_BACKUP_LOCK_NAME = "manual_backup"


def fetch_latest_backup_run() -> BackupRunRow | None:
    from app.db.pg_connection import fetchone

    return cast(
        BackupRunRow | None,
        fetchone(
            "SELECT id, started_at, finished_at, exit_code, result, bucket_key, "
            "size_bytes, log_tail, error_message "
            "FROM backup_runs ORDER BY started_at DESC LIMIT 1"
        ),
    )


def fetch_latest_backup_started_at() -> BackupStartedAtRow | None:
    from app.db.pg_connection import fetchone

    return cast(
        BackupStartedAtRow | None,
        fetchone(
            "SELECT started_at FROM backup_runs "
            "ORDER BY started_at DESC LIMIT 1"
        ),
    )


def fetch_manual_backup_lock() -> ManualBackupLockRow | None:
    from app.db.pg_connection import fetchone

    return cast(
        ManualBackupLockRow | None,
        fetchone(
            "SELECT lock_name, owner_token, owner_pid, acquired_at, expires_at "
            "FROM admin_backup_manual_locks WHERE lock_name = %s",
            (MANUAL_BACKUP_LOCK_NAME,),
        ),
    )


def try_acquire_manual_backup_lock(
    *, owner_token: str, ttl_seconds: int
) -> ManualBackupLockRow | None:
    """Atomically acquire the manual-backup lock if absent or expired."""
    from app.db.pg_connection import get_connection, put_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_backup_manual_locks (
                    lock_name, owner_token, owner_pid,
                    acquired_at, expires_at, updated_at
                )
                VALUES (
                    %s, %s, NULL,
                    now(), now() + (%s * interval '1 second'), now()
                )
                ON CONFLICT (lock_name) DO UPDATE
                SET owner_token = EXCLUDED.owner_token,
                    owner_pid = NULL,
                    acquired_at = now(),
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                WHERE admin_backup_manual_locks.expires_at <= now()
                RETURNING lock_name, owner_token, owner_pid,
                          acquired_at, expires_at
                """,
                (MANUAL_BACKUP_LOCK_NAME, owner_token, ttl_seconds),
            )
            row = cur.fetchone()
        conn.commit()
        return cast(ManualBackupLockRow | None, dict(row) if row else None)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def release_manual_backup_lock(*, owner_token: str | None = None) -> bool:
    from app.db.pg_connection import execute

    if owner_token is None:
        cur = execute(
            "DELETE FROM admin_backup_manual_locks WHERE lock_name = %s",
            (MANUAL_BACKUP_LOCK_NAME,),
        )
    else:
        cur = execute(
            "DELETE FROM admin_backup_manual_locks "
            "WHERE lock_name = %s AND owner_token = %s",
            (MANUAL_BACKUP_LOCK_NAME, owner_token),
        )
    return bool(getattr(cur, "rowcount", 0))
