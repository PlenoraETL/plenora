"""Key-value store condiviso (Plenora 2.0, Fase 0 piano unificazione).

Interfaccia unica per lo storage transitorio TTL oggi su Redis
(blacklist JWT, cache token, metadata task pending, lock leggeri).
In Fase 4 il backend diventa PostgreSQL + cache in-memory davanti,
senza toccare i consumer: dipendono da questo protocol, non da redis.

I rate limiter (sliding window login, GCRA arricchisci) NON passano da
qui: sono algoritmi con port propri — il KV generico ne sarebbe una
astrazione sbagliata (le primitive sorted-set/Lua non esistono su PG).
"""

from app.shared.kv.memory_store import InMemoryKeyValueStore
from app.shared.kv.pg_store import PgKeyValueStore
from app.shared.kv.store import KeyValueStore, KeyValueStoreUnavailable

__all__ = [
    "InMemoryKeyValueStore",
    "KeyValueStore",
    "KeyValueStoreUnavailable",
    "PgKeyValueStore",
]
