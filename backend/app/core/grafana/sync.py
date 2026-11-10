"""Sincronizzazione di cartelle, team e ruoli in Grafana (idempotente).

La sincronizzazione automatica dei team e' una funzione Enterprise: la fa
Plenora, con dati che il chiamante legge dal proprio DB (questo modulo non
importa ``auth``). Regole:

- per gruppo proprietario due cartelle: ``plenora-g-<gruppo>`` (Viewer =
  View, team ``admin-<gruppo>`` = Edit) e ``plenora-g-<gruppo>-riservata``
  (Viewer = View, nessun team: Eseguibili creati da un superadmin, che
  l'admin del gruppo non modifica, ``ownership.assert_can_modify``);
- membri del team = ESATTAMENTE gli admin attivi del gruppo: chi cambia
  ruolo o gruppo, e' disattivato o eliminato viene tolto alla
  sincronizzazione successiva; chi non e' mai entrato in Grafana viene
  creato con una richiesta auth-proxy prima di essere aggiunto;
- ruolo di organizzazione: chi in Plenora non e' superadmin ma in Grafana e'
  Admin (superadmin retrocesso) torna Viewer; il superadmin diventa Admin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.utils.logger import get_logger

from . import dashboard_builder
from .client import PERMISSION_EDIT, PERMISSION_VIEW, GrafanaApi

logger = get_logger(__name__)


@dataclass(frozen=True)
class PlenoraUser:
    """Utente Plenora come lo vede la sincronizzazione."""

    username: str
    role: str
    group_id: str
    active: bool = True


def _login_index(client: GrafanaApi) -> Mapping[str, Mapping[str, object]]:
    return {str(user.get("login")): user for user in client.org_users() if user.get("login")}


def ensure_team(client: GrafanaApi, group_id: str) -> int:
    name = dashboard_builder.team_name(group_id)
    team_id = client.find_team(name)
    return team_id if team_id is not None else client.create_team(name)


def ensure_folders(client: GrafanaApi, group_id: str, group_title: str, team_id: int) -> None:
    for reserved in (False, True):
        uid = dashboard_builder.folder_uid(group_id, reserved=reserved)
        if client.get_folder(uid) is None:
            title = f"Plenora — {group_title}" + (" (riservata)" if reserved else "")
            client.create_folder(uid, title)
        items: list[dict[str, object]] = [{"role": "Viewer", "permission": PERMISSION_VIEW}]
        if not reserved:
            items.append({"teamId": team_id, "permission": PERMISSION_EDIT})
        client.set_folder_permissions(uid, items)


def sync_team_members(client: GrafanaApi, team_id: int, admins: Sequence[PlenoraUser]) -> None:
    """Membri = admin attivi del gruppo (aggiunge i mancanti, toglie gli altri)."""
    desired = {user.username for user in admins if user.active and user.role == "admin"}
    index = _login_index(client)
    missing = [login for login in sorted(desired) if login not in index]
    for login in missing:
        client.ensure_proxy_user(login, admin=False)
    if missing:
        index = _login_index(client)
    current = client.team_member_logins(team_id)
    for login in sorted(desired - set(current)):
        user = index.get(login)
        if user is None:
            logger.warning(f"[grafana] utente {login} non creato in Grafana: non aggiunto al team")
            continue
        client.add_team_member(team_id, int(user["userId"]))
    for login in sorted(set(current) - desired):
        client.remove_team_member(team_id, int(current[login]))


def sync_org_roles(client: GrafanaApi, users: Iterable[PlenoraUser]) -> None:
    """Admin di Grafana = superadmin attivi di Plenora; tocca solo gli
    utenti che Plenora conosce (mai l'amministratore locale di Grafana)."""
    known = {user.username: user for user in users}
    for grafana_user in client.org_users():
        login = str(grafana_user.get("login") or "")
        plenora = known.get(login)
        if plenora is None:
            continue
        desired = "Admin" if plenora.active and plenora.role == "superadmin" else "Viewer"
        if str(grafana_user.get("role") or "") != desired:
            client.set_org_role(int(grafana_user["userId"]), desired)


def ensure_user(client: GrafanaApi, user: PlenoraUser) -> None:
    """L'utente che apre «Modifica in Grafana» esiste in Grafana."""
    if user.username not in _login_index(client):
        client.ensure_proxy_user(user.username, admin=user.role == "superadmin")


def sync_group(
    client: GrafanaApi,
    *,
    group_id: str,
    group_title: str,
    group_users: Sequence[PlenoraUser],
    all_users: Iterable[PlenoraUser],
    requester: PlenoraUser | None = None,
) -> None:
    """Una sincronizzazione completa per il gruppo proprietario."""
    team_id = ensure_team(client, group_id)
    ensure_folders(client, group_id, group_title, team_id)
    if requester is not None:
        ensure_user(client, requester)
    sync_team_members(client, team_id, [u for u in group_users if u.group_id == group_id])
    sync_org_roles(client, all_users)


__all__ = [
    "PlenoraUser",
    "ensure_folders",
    "ensure_team",
    "ensure_user",
    "sync_group",
    "sync_org_roles",
    "sync_team_members",
]
