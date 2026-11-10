"""Accesso a /grafana: allineamento di utente, ruolo e team al forward_auth.

Il forward_auth (``auth.grafana_router``) rilegge l'utente dal DB a ogni
richiesta e, PRIMA di lasciarlo passare, chiama :func:`ensure_access`: se
l'impronta dell'accesso (login, nome, ruolo, team attesi:
``sync.access_fingerprint``) e' diversa dall'ultima applicata, o e' passato
``GRAFANA_ACCESS_SYNC_TTL_SECONDS``, porta Grafana allo stato atteso
(``sync.apply_user_access``). Cosi' un admin spostato di gruppo o retrocesso,
o un superadmin retrocesso, e' riallineato alla sua prima richiesta, senza
aspettare una sincronizzazione da «Modifica in Grafana».

Cache nel processo, per utente: una sola sincronizzazione alla volta per
utente (le richieste parallele della stessa pagina aspettano la prima), poi
nessuna chiamata a Grafana finche' l'impronta non cambia o la voce scade.
Errore di Grafana: nessuna voce in cache e l'errore risale (il forward_auth
risponde 503: mai lasciar passare con team non verificati).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from . import client as grafana_client
from . import settings as grafana_settings
from . import sync

_MAX_ENTRIES = 10_000


class _AccessCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[tuple[Any, ...], float]] = {}
        self._user_locks: dict[str, threading.Lock] = {}

    def _user_lock(self, user_id: str) -> threading.Lock:
        with self._lock:
            lock = self._user_locks.get(user_id)
            if lock is None:
                if len(self._user_locks) > _MAX_ENTRIES:
                    self._user_locks = {k: v for k, v in self._user_locks.items() if v.locked()}
                lock = self._user_locks[user_id] = threading.Lock()
            return lock

    def ensure(
        self,
        user: sync.PlenoraUser,
        *,
        client_factory: Callable[[], grafana_client.GrafanaApi] = grafana_client.get_client,
        now: Optional[float] = None,
    ) -> bool:
        """True se ha chiamato Grafana, False se la voce in cache bastava."""
        fingerprint = sync.access_fingerprint(user)
        with self._user_lock(user.user_id):
            current = time.monotonic() if now is None else now
            entry = self._entries.get(user.user_id)
            if entry is not None and entry[0] == fingerprint and entry[1] > current:
                return False
            self._entries.pop(user.user_id, None)
            sync.apply_user_access(client_factory(), user)
            with self._lock:
                if len(self._entries) > _MAX_ENTRIES:
                    self._entries = {k: v for k, v in self._entries.items() if v[1] > current}
                self._entries[user.user_id] = (fingerprint, current + grafana_settings.access_sync_ttl_seconds())
            return True

    def forget(self, user_id: Optional[str] = None) -> None:
        with self._lock:
            if user_id is None:
                self._entries.clear()
            else:
                self._entries.pop(user_id, None)


_cache = _AccessCache()


def ensure_access(user: sync.PlenoraUser, *, now: Optional[float] = None) -> bool:
    return _cache.ensure(user, now=now)


def forget(user_id: Optional[str] = None) -> None:
    """Prossimo accesso = sincronizzazione completa (test, «Modifica in Grafana»)."""
    _cache.forget(user_id)


__all__ = ["ensure_access", "forget"]
