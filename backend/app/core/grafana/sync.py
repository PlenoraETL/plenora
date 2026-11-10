"""Identita', team e cartelle in Grafana (idempotente) — correzione 11/11/2026.

La sincronizzazione automatica dei team e' una funzione Enterprise: la fa
Plenora, con dati che il chiamante legge dal proprio DB (questo modulo non
importa ``auth``). Tre regole, ognuna in UNA funzione:

1. **Identita'** (:func:`grafana_login`, :func:`proxy_headers`): l'utente
   Grafana e' legato all'ID dell'utente Plenora (``plenora-u-<id>``), mai
   allo username: uno username liberato da una lapide e riusato da un utente
   nuovo NON eredita l'utente Grafana precedente (ruolo, team). Il nome
   visualizzato viaggia a parte (``X-WEBAUTH-NAME``). Il ruolo
   dell'organizzazione viaggia SEMPRE (``X-WEBAUTH-ROLE``: Admin al
   superadmin, Viewer a tutti gli altri): Grafana lo riallinea a ogni accesso
   (provato dal vivo su Grafana 13.3: Admin -> Viewer -> Admin con la sola
   intestazione; senza intestazione il ruolo resta quello vecchio).
2. **Team di un utente** (:func:`expected_teams`): ``utenti-<gruppo>`` per
   ogni utente attivo del gruppo, piu' ``admin-<gruppo>`` per l'admin.
   :func:`apply_user_access` porta l'appartenenza in Grafana ESATTAMENTE a
   questa (toglie dai team gestiti che non spettano, aggiunge ai mancanti):
   la chiama il forward_auth (``grafana.access``) prima di lasciar passare,
   quindi chi cambia ruolo o gruppo e' riallineato alla prima richiesta.
3. **Visibilita' di una dashboard** (:func:`folder_permissions`): una
   cartella per Eseguibile; View ai team ``utenti-<g>`` dei gruppi che
   vedono l'Eseguibile in Plenora (proprietario + condivisioni), View al
   ruolo Viewer SOLO se l'Eseguibile e' condiviso con ``*`` (tutti), Edit al
   team ``admin-<proprietario>`` solo se un admin del gruppo puo' modificare
   l'Eseguibile. Nessun View «a tutti» altrimenti: un utente non vede
   elenco ne' JSON delle dashboard di un altro istituto.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.utils.logger import get_logger

from .client import PERMISSION_EDIT, PERMISSION_VIEW, GrafanaApi, GrafanaError

logger = get_logger(__name__)

LOGIN_PREFIX = "plenora-u-"
USERS_TEAM_PREFIX = "utenti-"
ADMINS_TEAM_PREFIX = "admin-"
MANAGED_TEAM_PREFIXES = (USERS_TEAM_PREFIX, ADMINS_TEAM_PREFIX)

USER_HEADER = "X-WEBAUTH-USER"
NAME_HEADER = "X-WEBAUTH-NAME"
ROLE_HEADER = "X-WEBAUTH-ROLE"
ROLE_ADMIN = "Admin"
ROLE_VIEWER = "Viewer"

_LOGIN_SAFE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_NAME_MAX = 100


@dataclass(frozen=True)
class PlenoraUser:
    """Utente Plenora come lo vede Grafana (letto dal DB a ogni uso)."""

    user_id: str
    username: str
    role: str
    group_id: str
    active: bool = True


# ── 1. Identita' ────────────────────────────────────────────────────────────

def grafana_login(user_id: str) -> str:
    """Login Grafana di un utente Plenora: dall'ID, stabile e mai riusato."""
    raw = str(user_id)
    if _LOGIN_SAFE.fullmatch(raw):
        return f"{LOGIN_PREFIX}{raw}"
    return f"{LOGIN_PREFIX}h-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def display_name(username: str) -> str:
    """Nome visualizzato in Grafana: lo username in ASCII stampabile (le
    intestazioni HTTP non portano Unicode; i caratteri fuori diventano «?»)."""
    text = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(username))
    return (text.strip() or "?")[:_NAME_MAX]


def org_role(user: PlenoraUser) -> str:
    return ROLE_ADMIN if user.active and user.role == "superadmin" else ROLE_VIEWER


def proxy_headers(user: PlenoraUser) -> Mapping[str, str]:
    """Le intestazioni auth-proxy di un utente: le stesse dal forward_auth e
    dalle richieste del backend (creazione dell'utente prima del team)."""
    return {
        USER_HEADER: grafana_login(user.user_id),
        NAME_HEADER: display_name(user.username),
        ROLE_HEADER: org_role(user),
    }


# ── 2. Team di un utente ────────────────────────────────────────────────────

def users_team(group_id: str) -> str:
    return f"{USERS_TEAM_PREFIX}{group_id}"


def admins_team(group_id: str) -> str:
    return f"{ADMINS_TEAM_PREFIX}{group_id}"


def is_managed_team(name: object) -> bool:
    return isinstance(name, str) and name.startswith(MANAGED_TEAM_PREFIXES)


def expected_teams(user: PlenoraUser) -> frozenset[str]:
    if not user.active or not user.group_id:
        return frozenset()
    teams = {users_team(user.group_id)}
    if user.role == "admin":
        teams.add(admins_team(user.group_id))
    return frozenset(teams)


def access_fingerprint(user: PlenoraUser) -> tuple[Any, ...]:
    """Tutto cio' che decide l'accesso in Grafana: se cambia, si risincronizza."""
    return (tuple(sorted(proxy_headers(user).items())), tuple(sorted(expected_teams(user))))


def ensure_team(client: GrafanaApi, name: str) -> int:
    team_id = client.find_team(name)
    return team_id if team_id is not None else client.create_team(name)


def apply_user_access(client: GrafanaApi, user: PlenoraUser) -> None:
    """Crea/aggiorna l'utente in Grafana (richiesta auth-proxy con le sue
    intestazioni: login, nome, ruolo) e porta i suoi team gestiti
    ESATTAMENTE a :func:`expected_teams`."""
    headers = proxy_headers(user)
    me = client.proxy_get(headers, "/api/user")
    if not isinstance(me, Mapping) or not isinstance(me.get("id"), int):
        raise GrafanaError(502, "auth proxy: utente non creato (whitelist o [auth.proxy] da verificare)")
    grafana_user_id = int(me["id"])
    current = {
        str(team.get("name")): int(team["id"])
        for team in (client.proxy_get(headers, "/api/user/teams") or [])
        if isinstance(team, Mapping) and is_managed_team(team.get("name"))
    }
    expected = expected_teams(user)
    for name in sorted(set(current) - expected):
        client.remove_team_member(current[name], grafana_user_id)
    for name in sorted(expected - set(current)):
        client.add_team_member(ensure_team(client, name), grafana_user_id)


def prune_team(client: GrafanaApi, name: str, allowed: Iterable[PlenoraUser]) -> None:
    """Toglie dal team gli utenti Plenora a cui non spetta piu' (disattivati,
    eliminati, spostati, retrocessi). Non aggiunge nessuno (lo fa
    :func:`apply_user_access` al loro accesso) e non tocca gli utenti che
    Plenora non gestisce (service account, amministratore locale)."""
    team_id = client.find_team(name)
    if team_id is None:
        return
    keep = {grafana_login(user.user_id) for user in allowed if name in expected_teams(user)}
    for login, member_id in client.team_member_logins(team_id).items():
        if login.startswith(LOGIN_PREFIX) and login not in keep:
            client.remove_team_member(team_id, int(member_id))


# ── 3. Visibilita' di una dashboard ─────────────────────────────────────────

@dataclass(frozen=True)
class FolderAccess:
    """Chi vede e chi modifica la cartella di UN Eseguibile."""

    viewer_groups: frozenset[str]
    everyone: bool = False
    editor_group: Optional[str] = None


def folder_permissions(client: GrafanaApi, access: FolderAccess) -> list[Mapping[str, int | str]]:
    items: list[Mapping[str, int | str]] = []
    if access.everyone:
        items.append({"role": ROLE_VIEWER, "permission": PERMISSION_VIEW})
    for group_id in sorted(access.viewer_groups):
        items.append({"teamId": ensure_team(client, users_team(group_id)), "permission": PERMISSION_VIEW})
    if access.editor_group:
        items.append({"teamId": ensure_team(client, admins_team(access.editor_group)),
                      "permission": PERMISSION_EDIT})
    return items


def sync_folder(client: GrafanaApi, *, folder_uid: str, title: str, access: FolderAccess) -> None:
    """Cartella dell'Eseguibile con ESATTAMENTE i permessi della regola
    (``set_folder_permissions`` sostituisce l'elenco intero)."""
    if client.get_folder(folder_uid) is None:
        client.create_folder(folder_uid, title)
    client.set_folder_permissions(folder_uid, folder_permissions(client, access))


def prune_teams(client: GrafanaApi, access: FolderAccess, users_by_group: Mapping[str, Sequence[PlenoraUser]]) -> None:
    """Pulizia dei team toccati dalla cartella (difesa in profondita':
    l'accesso vero si riallinea comunque al forward_auth)."""
    for group_id in sorted(access.viewer_groups):
        prune_team(client, users_team(group_id), users_by_group.get(group_id, ()))
    if access.editor_group:
        prune_team(client, admins_team(access.editor_group), users_by_group.get(access.editor_group, ()))


__all__ = [
    "ADMINS_TEAM_PREFIX",
    "FolderAccess",
    "LOGIN_PREFIX",
    "NAME_HEADER",
    "PlenoraUser",
    "ROLE_ADMIN",
    "ROLE_HEADER",
    "ROLE_VIEWER",
    "USERS_TEAM_PREFIX",
    "USER_HEADER",
    "access_fingerprint",
    "admins_team",
    "apply_user_access",
    "display_name",
    "ensure_team",
    "expected_teams",
    "folder_permissions",
    "grafana_login",
    "is_managed_team",
    "org_role",
    "prune_team",
    "prune_teams",
    "proxy_headers",
    "sync_folder",
    "users_team",
]
