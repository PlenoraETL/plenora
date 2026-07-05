"""PgKeyValueStore: backend PostgreSQL del KeyValueStore.

Stessa semantica del protocol (blindata da test_key_value_store_contract +
e2e): TTL via ``expires_at``, get/exists/scan filtrano le righe scadute,
``acquire_lock`` e' un set-if-not-exists atomico (upsert che acquisisce solo
se la riga e' assente o scaduta). Letture fail-soft (None/[]/False),
scritture fail-loud (``KeyValueStoreUnavailable``) come gli altri backend.

In produzione una cache in-memory davanti assorbe le hot path (blacklist).
"""

from __future__ import annotations

from typing import Optional

from app.db.pg_connection import unit_of_work
from app.shared.kv.store import KeyValueStoreUnavailable


def _like_prefix(prefix: str) -> str:
    """Escapa i metacaratteri LIKE nel prefisso e appende '%'."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


class PgKeyValueStore:
    """KeyValueStore su PostgreSQL (tabella kv_store)."""

    def get(self, key: str) -> Optional[str]:
        try:
            with unit_of_work() as session:
                row = session.fetchone(
                    "SELECT value FROM kv_store "
                    "WHERE key = %s AND (expires_at IS NULL OR expires_at > now())",
                    (key,),
                )
        except Exception:
            return None
        return row["value"] if row else None

    def set(self, key: str, value: str, *, ttl_seconds: Optional[int] = None) -> None:
        try:
            with unit_of_work() as session:
                if ttl_seconds is None:
                    session.execute(
                        "INSERT INTO kv_store (key, value, expires_at, updated_at) "
                        "VALUES (%s, %s, NULL, now()) "
                        "ON CONFLICT (key) DO UPDATE "
                        "SET value = EXCLUDED.value, expires_at = NULL, updated_at = now()",
                        (key, value),
                    )
                else:
                    session.execute(
                        "INSERT INTO kv_store (key, value, expires_at, updated_at) "
                        "VALUES (%s, %s, now() + (%s * interval '1 second'), now()) "
                        "ON CONFLICT (key) DO UPDATE "
                        "SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at, "
                        "updated_at = now()",
                        (key, value, ttl_seconds),
                    )
        except Exception as exc:
            raise KeyValueStoreUnavailable(str(exc)) from exc

    def delete(self, key: str) -> bool:
        try:
            with unit_of_work() as session:
                cur = session.execute("DELETE FROM kv_store WHERE key = %s", (key,))
            return bool(getattr(cur, "rowcount", 0))
        except Exception:
            return False

    def exists(self, key: str) -> bool:
        try:
            with unit_of_work() as session:
                row = session.fetchone(
                    "SELECT 1 AS one FROM kv_store "
                    "WHERE key = %s AND (expires_at IS NULL OR expires_at > now())",
                    (key,),
                )
            return row is not None
        except Exception:
            return False

    def scan_keys(self, prefix: str) -> list[str]:
        try:
            with unit_of_work() as session:
                rows = session.fetchall(
                    "SELECT key FROM kv_store "
                    "WHERE key LIKE %s ESCAPE '\\' "
                    "AND (expires_at IS NULL OR expires_at > now()) "
                    "ORDER BY key",
                    (_like_prefix(prefix),),
                )
            return [row["key"] for row in rows]
        except Exception:
            return []

    def acquire_lock(
        self, key: str, *, ttl_seconds: int, owner: Optional[str] = None
    ) -> bool:
        # Set-if-not-exists atomico: INSERT, o UPDATE solo se il lock e'
        # scaduto. Un lock vivo di un altro -> WHERE falso -> nessuna riga
        # in RETURNING -> non acquisito. Il valore memorizza l'owner (o "1").
        value = owner or "1"
        try:
            with unit_of_work() as session:
                with session.cursor() as cur:
                    cur.execute(
                        "INSERT INTO kv_store (key, value, expires_at, updated_at) "
                        "VALUES (%s, %s, now() + (%s * interval '1 second'), now()) "
                        "ON CONFLICT (key) DO UPDATE "
                        "SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at, "
                        "updated_at = now() "
                        "WHERE kv_store.expires_at IS NOT NULL "
                        "AND kv_store.expires_at <= now() "
                        "RETURNING key",
                        (key, value, ttl_seconds),
                    )
                    row = cur.fetchone()
            return row is not None
        except Exception as exc:
            raise KeyValueStoreUnavailable(str(exc)) from exc

    def release_lock(self, key: str, *, owner: Optional[str] = None) -> None:
        # Owner-safe: con owner cancella solo se il valore e' il nostro owner
        # (no release del lock altrui dopo un takeover post-scadenza).
        try:
            with unit_of_work() as session:
                if owner is None:
                    session.execute("DELETE FROM kv_store WHERE key = %s", (key,))
                else:
                    session.execute(
                        "DELETE FROM kv_store WHERE key = %s AND value = %s",
                        (key, owner),
                    )
        except Exception:
            pass


__all__ = ["PgKeyValueStore"]
