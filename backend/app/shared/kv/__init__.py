"""Key-value store condiviso per dati transitori TTL.

Interfaccia unica per lo storage transitorio TTL
(blacklist JWT, cache token, metadata task pending, lock leggeri).
Il backend di produzione e' PostgreSQL + cache in-memory davanti; i
consumer dipendono da questo protocol, non dal backend specifico.

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
