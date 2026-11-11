"""Chi deve sapere che le condivisioni di una risorsa sono cambiate.

Le condivisioni (``*_shares``) si scrivono SOLO da ``shares_router`` (set,
add, remove). Un dominio che tiene una copia della visibilita' fuori dal DB
(oggi: la cartella Grafana della dashboard di un Eseguibile,
``eseguibili.dashboard``) registra qui una funzione per la sua tabella; il
router la chiama dopo ogni scrittura. ``auth`` non importa i domini che lo
usano: sono loro a registrarsi (nessun ciclo).

Una funzione che fallisce non annulla la condivisione gia' scritta: l'errore
si registra nel log e la funzione stessa deve dichiarare come si riallinea.
"""

from __future__ import annotations

import threading
from typing import Callable

from app.utils.logger import get_logger

logger = get_logger(__name__)

ShareHook = Callable[[str], None]

_lock = threading.Lock()
_hooks: dict[str, list[ShareHook]] = {}


def register(resource_table: str, hook: ShareHook) -> None:
    """Idempotente: la stessa funzione registrata due volte resta una."""
    with _lock:
        hooks = _hooks.setdefault(resource_table, [])
        if hook not in hooks:
            hooks.append(hook)


def registered(resource_table: str) -> tuple[ShareHook, ...]:
    with _lock:
        return tuple(_hooks.get(resource_table, ()))


GroupDeletedHook = Callable[[str], None]
_group_deleted_hooks: list[GroupDeletedHook] = []


def register_group_deleted(hook: GroupDeletedHook) -> None:
    """Chi tiene una copia fuori dal DB di cio' che dipende da un gruppo
    (oggi: team e cartelle Grafana, ``eseguibili.dashboard``) si registra
    qui; il router dei gruppi la chiama DOPO la cancellazione riuscita.
    Idempotente."""
    with _lock:
        if hook not in _group_deleted_hooks:
            _group_deleted_hooks.append(hook)


def registered_group_deleted() -> tuple[GroupDeletedHook, ...]:
    with _lock:
        return tuple(_group_deleted_hooks)


def notify_group_deleted(group_id: str) -> None:
    """Best effort dichiarato: la cancellazione del gruppo e' gia' fatta."""
    for hook in registered_group_deleted():
        try:
            hook(group_id)
        except Exception:  # best effort dichiarato (come notify)
            logger.warning(f"[gruppi] riallineamento dopo la cancellazione del gruppo {group_id} non riuscito",
                           exc_info=True)


def notify(resource_table: str, resource_id: str) -> None:
    for hook in registered(resource_table):
        try:
            hook(resource_id)
        except Exception:  # best effort dichiarato: la condivisione e' gia' scritta
            logger.warning(
                f"[shares] riallineamento dopo la modifica delle condivisioni di {resource_table}/{resource_id} "
                "non riuscito", exc_info=True,
            )


__all__ = [
    "GroupDeletedHook",
    "ShareHook",
    "notify",
    "notify_group_deleted",
    "register",
    "register_group_deleted",
    "registered",
    "registered_group_deleted",
]
