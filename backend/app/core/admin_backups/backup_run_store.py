"""DB access for admin backup run metadata."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from typing_extensions import TypedDict

from app.db.pg_connection import unit_of_work


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
    with unit_of_work() as session:
        return cast(
            BackupRunRow | None,
            session.fetchone(
                "SELECT id, started_at, finished_at, exit_code, result, bucket_key, "
                "size_bytes, log_tail, error_message "
                "FROM backup_runs ORDER BY started_at DESC LIMIT 1"
            ),
        )


def fetch_latest_backup_started_at() -> BackupStartedAtRow | None:
    with unit_of_work() as session:
        return cast(
            BackupStartedAtRow | None,
            session.fetchone(
                "SELECT started_at FROM backup_runs "
                "ORDER BY started_at DESC LIMIT 1"
            ),
        )


def fetch_manual_backup_lock() -> ManualBackupLockRow | None:
    with unit_of_work() as session:
        return cast(
            ManualBackupLockRow | None,
            session.fetchone(
                "SELECT lock_name, owner_token, owner_pid, acquired_at, expires_at "
                "FROM admin_backup_manual_locks WHERE lock_name = %s",
                (MANUAL_BACKUP_LOCK_NAME,),
            ),
        )


def try_acquire_manual_backup_lock(
    *, owner_token: str, ttl_seconds: int
) -> ManualBackupLockRow | None:
    """Atomically acquire the manual-backup lock if absent or expired."""
    with unit_of_work() as session:
        with session.cursor() as cur:
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
        return cast(ManualBackupLockRow | None, dict(row) if row else None)


def refresh_manual_backup_lock(
    *, owner_token: str, ttl_seconds: int
) -> bool:
    """Estendi expires_at del lock se il caller ne e' ancora owner.

    Heartbeat-based liveness: invece di tenere il lock con un TTL
    enorme (precedentemente 7200s = 2h) che lasciava lock orfani per
    ore quando un worker del backup crashava, riduciamo TTL a un valore
    breve e il task in esecuzione estende `expires_at` periodicamente.
    Se il worker muore, l'estensione si ferma, TTL scade in ≤TTL
    secondi, e il prossimo `try_acquire_manual_backup_lock` riacquisisce
    via clausola `WHERE expires_at <= now()`.

    L'UPDATE include `owner_token = %s` nel WHERE per evitare che un
    thread heartbeat zombi rinnovi un lock di cui non e' piu' owner
    (es. dopo un takeover). Ritorna True se la riga e' stata refreshata,
    False se il lock non esiste piu' o e' di un altro owner.
    """
    with unit_of_work() as session:
        cur = session.execute(
            """
            UPDATE admin_backup_manual_locks
            SET expires_at = now() + (%s * interval '1 second'),
                updated_at = now()
            WHERE lock_name = %s AND owner_token = %s
            """,
            (ttl_seconds, MANUAL_BACKUP_LOCK_NAME, owner_token),
        )
    return bool(getattr(cur, "rowcount", 0))


def release_manual_backup_lock(*, owner_token: str | None = None) -> bool:
    with unit_of_work() as session:
        if owner_token is None:
            cur = session.execute(
                "DELETE FROM admin_backup_manual_locks WHERE lock_name = %s",
                (MANUAL_BACKUP_LOCK_NAME,),
            )
        else:
            cur = session.execute(
                "DELETE FROM admin_backup_manual_locks "
                "WHERE lock_name = %s AND owner_token = %s",
                (MANUAL_BACKUP_LOCK_NAME, owner_token),
            )
    return bool(getattr(cur, "rowcount", 0))
