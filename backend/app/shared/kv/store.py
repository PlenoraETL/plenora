"""Protocol KeyValueStore — contratto dello storage transitorio TTL.

Semantica (vincolante per ogni implementazione, blindata dal contract
test `tests2/shared/test_key_value_store_contract.py`):

- valori `str` (la serializzazione e' responsabilita' del consumer);
- `set` con `ttl_seconds=None` = nessuna scadenza; con ttl, la chiave
  sparisce dopo la scadenza (get -> None, exists -> False);
- `get` su chiave assente/scaduta -> None (mai eccezione);
- `delete` idempotente (True se la chiave esisteva);
- `scan_keys(prefix)` ritorna le chiavi vive con quel prefisso;
- `acquire_lock` e' atomico (set-if-not-exists con TTL): True solo per
  il primo chiamante finche' il lock non scade o viene rilasciato.

Errori di infrastruttura (backend irraggiungibile): le implementazioni
sollevano `KeyValueStoreUnavailable` su `set`/`acquire_lock` (scritture
fail-loud); i consumer decidono la policy (fail-open/closed) come oggi.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


class KeyValueStoreUnavailable(RuntimeError):
    """Backend KV irraggiungibile o in errore."""


@runtime_checkable
class KeyValueStore(Protocol):
    """Storage chiave/valore transitorio con TTL e lock atomici."""

    def get(self, key: str) -> Optional[str]:
        ...

    def set(self, key: str, value: str, *, ttl_seconds: Optional[int] = None) -> None:
        ...

    def delete(self, key: str) -> bool:
        ...

    def exists(self, key: str) -> bool:
        ...

    def scan_keys(self, prefix: str) -> list[str]:
        ...

    def acquire_lock(self, key: str, *, ttl_seconds: int) -> bool:
        ...

    def release_lock(self, key: str) -> None:
        ...


__all__ = ["KeyValueStore", "KeyValueStoreUnavailable"]
