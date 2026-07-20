"""Cross-host leases for consume-once executable downloads."""

from __future__ import annotations

from app.db.pg_connection import unit_of_work


DEFAULT_LEASE_SECONDS = 300
DEFAULT_PURGE_BATCH_SIZE = 1000


def try_acquire(
    *, task_id: str, group_id: str, owner_token: str,
    ttl_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """Atomically claim a task output, replacing only an expired lease."""
    with unit_of_work() as session:
        with session.cursor() as cur:
            cur.execute(
                """
                INSERT INTO eseguibili_download_claims (
                    task_id, group_id, owner_token, acquired_at,
                    lease_expires_at, updated_at
                )
                VALUES (
                    %s, %s, %s, now(),
                    now() + (%s * interval '1 second'), now()
                )
                ON CONFLICT (task_id, group_id) DO UPDATE
                SET owner_token = EXCLUDED.owner_token,
                    acquired_at = now(),
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    updated_at = now()
                WHERE eseguibili_download_claims.lease_expires_at <= now()
                RETURNING task_id
                """,
                (task_id, group_id, owner_token, ttl_seconds),
            )
            return cur.fetchone() is not None


def refresh(
    *, task_id: str, group_id: str, owner_token: str,
    ttl_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    with unit_of_work() as session:
        cur = session.execute(
            """
            UPDATE eseguibili_download_claims
            SET lease_expires_at = now() + (%s * interval '1 second'),
                updated_at = now()
            WHERE task_id = %s AND group_id = %s AND owner_token = %s
            """,
            (ttl_seconds, task_id, group_id, owner_token),
        )
    return bool(getattr(cur, "rowcount", 0))


def release(*, task_id: str, group_id: str, owner_token: str) -> bool:
    with unit_of_work() as session:
        cur = session.execute(
            """
            DELETE FROM eseguibili_download_claims
            WHERE task_id = %s AND group_id = %s AND owner_token = %s
            """,
            (task_id, group_id, owner_token),
        )
    return bool(getattr(cur, "rowcount", 0))


def purge_expired(*, limit: int = DEFAULT_PURGE_BATCH_SIZE) -> int:
    """Delete an ordered, bounded batch of expired download leases."""
    batch_size = max(1, min(int(limit), 10_000))
    with unit_of_work() as session:
        cur = session.execute(
            """
            WITH expired AS (
                SELECT task_id, group_id
                FROM eseguibili_download_claims
                WHERE lease_expires_at <= now()
                ORDER BY lease_expires_at
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            DELETE FROM eseguibili_download_claims AS claims
            USING expired
            WHERE claims.task_id = expired.task_id
              AND claims.group_id = expired.group_id
            """,
            (batch_size,),
        )
    return int(getattr(cur, "rowcount", 0) or 0)


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_PURGE_BATCH_SIZE",
    "purge_expired",
    "refresh",
    "release",
    "try_acquire",
]
