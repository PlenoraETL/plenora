"""Leader election del beat su PostgreSQL (Plenora 2.0, Fase 3).

Rimpiazza il lock leader Redis (SETNX/EXPIRE). Pattern TTL + refresh come
``admin_backup_manual_locks``: ``acquire_or_refresh`` e' un singolo upsert
atomico che acquisisce il lock se libero/scaduto OPPURE rinnova ``expires_at``
se il caller ne e' gia' owner. Pool-safe (niente advisory lock di sessione).
"""

from __future__ import annotations

from typing import Optional, cast

from app.db.pg_connection import unit_of_work

BEAT_LEADER_LOCK_NAME = "beat_leader"


def acquire_or_refresh_beat_leader(*, owner_token: str, ttl_seconds: int) -> bool:
    """Acquisisce o rinnova il lock leader in modo atomico.

    Un solo statement copre i tre casi:
      - riga assente            -> INSERT, divento leader;
      - riga scaduta            -> UPDATE (WHERE expires_at <= now()), divento leader;
      - riga mia (refresh)      -> UPDATE (WHERE owner_token = me), resto leader;
      - riga di un altro, viva  -> nessun update, NON sono leader.

    Ritorna True se dopo lo statement il lock e' mio.
    """
    with unit_of_work() as session:
        with session.cursor() as cur:
            cur.execute(
                """
                INSERT INTO beat_leader_locks (
                    lock_name, owner_token, acquired_at, expires_at, updated_at
                )
                VALUES (
                    %s, %s, now(), now() + (%s * interval '1 second'), now()
                )
                ON CONFLICT (lock_name) DO UPDATE
                SET owner_token = EXCLUDED.owner_token,
                    acquired_at = CASE
                        WHEN beat_leader_locks.owner_token = EXCLUDED.owner_token
                        THEN beat_leader_locks.acquired_at
                        ELSE now()
                    END,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                WHERE beat_leader_locks.expires_at <= now()
                   OR beat_leader_locks.owner_token = EXCLUDED.owner_token
                RETURNING owner_token
                """,
                (BEAT_LEADER_LOCK_NAME, owner_token, ttl_seconds),
            )
            row = cur.fetchone()
    return bool(row) and row["owner_token"] == owner_token


def release_beat_leader(*, owner_token: str) -> bool:
    """Rilascia il lock se il caller ne e' ancora owner (release safe).

    Filtra per owner_token nel WHERE: un beat che ha gia' perso la
    leadership (TTL scaduto, takeover) non deve cancellare il lock di un
    altro. Ritorna True se la riga e' stata cancellata."""
    with unit_of_work() as session:
        cur = session.execute(
            "DELETE FROM beat_leader_locks "
            "WHERE lock_name = %s AND owner_token = %s",
            (BEAT_LEADER_LOCK_NAME, owner_token),
        )
    return bool(getattr(cur, "rowcount", 0))


def current_beat_leader() -> Optional[str]:
    """owner_token del leader corrente (lock non scaduto), o None.

    Per telemetria/health: non partecipa all'acquisizione."""
    with unit_of_work() as session:
        row = session.fetchone(
            "SELECT owner_token FROM beat_leader_locks "
            "WHERE lock_name = %s AND expires_at > now()",
            (BEAT_LEADER_LOCK_NAME,),
        )
    return cast(Optional[str], row["owner_token"]) if row else None


__all__ = [
    "BEAT_LEADER_LOCK_NAME",
    "acquire_or_refresh_beat_leader",
    "current_beat_leader",
    "release_beat_leader",
]
