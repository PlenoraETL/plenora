"""Client delle API HTTP di Grafana (service account Plenora).

Porta unica verso Grafana: ``get_client()``. Con ``GRAFANA_ENABLED=false``
solleva ``GrafanaDisabled`` PRIMA di creare qualunque connessione: nessun
percorso puo' chiamare Grafana con la funzione spenta (per costruzione).
Nei test ``set_client_factory`` sostituisce il client (nessuna rete).

Errori: ``GrafanaUnavailable`` (rete, timeout, 5xx, token assente: si
riprova), ``GrafanaError`` (4xx: richiesta rifiutata, con lo stato).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

import requests

from app.utils.logger import get_logger

from . import settings as grafana_settings

logger = get_logger(__name__)

# Permessi di cartella di Grafana.
PERMISSION_VIEW = 1
PERMISSION_EDIT = 2


class GrafanaDisabled(RuntimeError):
    """``GRAFANA_ENABLED`` e' falso."""


class GrafanaUnavailable(RuntimeError):
    """Grafana non risponde o non e' configurato (si puo' riprovare)."""


class GrafanaError(RuntimeError):
    """Grafana ha rifiutato la richiesta (4xx)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Grafana {status_code}: {message}")
        self.status_code = status_code


class GrafanaApi(Protocol):
    """Superficie usata da Plenora (il client finto dei test la implementa)."""

    def get_folder(self, uid: str) -> Optional[Mapping[str, Any]]: ...
    def create_folder(self, uid: str, title: str) -> Mapping[str, Any]: ...
    def set_folder_permissions(self, uid: str, items: Sequence[Mapping[str, Any]]) -> None: ...
    def find_team(self, name: str) -> Optional[int]: ...
    def create_team(self, name: str) -> int: ...
    def team_member_logins(self, team_id: int) -> Mapping[str, int]: ...
    def add_team_member(self, team_id: int, user_id: int) -> None: ...
    def remove_team_member(self, team_id: int, user_id: int) -> None: ...
    def org_users(self) -> Sequence[Mapping[str, Any]]: ...
    def set_org_role(self, user_id: int, role: str) -> None: ...
    def ensure_proxy_user(self, login: str, *, admin: bool) -> None: ...
    def get_dashboard(self, uid: str) -> Optional[Mapping[str, Any]]: ...
    def upsert_dashboard(self, dashboard: Mapping[str, Any], *, folder_uid: str, message: str) -> Mapping[str, Any]: ...
    def delete_dashboard(self, uid: str) -> bool: ...


class HttpGrafanaClient:
    """Implementazione reale con ``requests`` e Bearer del service account."""

    def __init__(self, base_url: str, token: str, *, timeout: float) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._session = requests.Session()

    def _request(self, method: str, path: str, *, ok404: bool = False, auth: bool = True,
                 headers: Optional[Mapping[str, str]] = None, **kwargs: Any) -> Any:
        request_headers = {"Accept": "application/json", **(headers or {})}
        if auth:
            request_headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self._session.request(
                method, f"{self._base}{path}", headers=request_headers, timeout=self._timeout, **kwargs,
            )
        except requests.RequestException as exc:
            raise GrafanaUnavailable(f"Grafana non raggiungibile ({type(exc).__name__})") from exc
        if ok404 and response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise GrafanaUnavailable(f"Grafana {response.status_code}")
        if response.status_code >= 400:
            raise GrafanaError(response.status_code, response.text[:300])
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    def get_folder(self, uid: str) -> Optional[Mapping[str, Any]]:
        return self._request("GET", f"/api/folders/{uid}", ok404=True)

    def create_folder(self, uid: str, title: str) -> Mapping[str, Any]:
        return self._request("POST", "/api/folders", json={"uid": uid, "title": title})

    def set_folder_permissions(self, uid: str, items: Sequence[Mapping[str, Any]]) -> None:
        self._request("POST", f"/api/folders/{uid}/permissions", json={"items": list(items)})

    def find_team(self, name: str) -> Optional[int]:
        found = self._request("GET", "/api/teams/search", params={"name": name}) or {}
        for team in found.get("teams") or []:
            if team.get("name") == name:
                return int(team["id"])
        return None

    def create_team(self, name: str) -> int:
        return int(self._request("POST", "/api/teams", json={"name": name})["teamId"])

    def team_member_logins(self, team_id: int) -> Mapping[str, int]:
        members = self._request("GET", f"/api/teams/{team_id}/members") or []
        return {str(m.get("login")): int(m.get("userId")) for m in members if m.get("login")}

    def add_team_member(self, team_id: int, user_id: int) -> None:
        self._request("POST", f"/api/teams/{team_id}/members", json={"userId": user_id})

    def remove_team_member(self, team_id: int, user_id: int) -> None:
        self._request("DELETE", f"/api/teams/{team_id}/members/{user_id}", ok404=True)

    def org_users(self) -> Sequence[Mapping[str, Any]]:
        return list(self._request("GET", "/api/org/users") or [])

    def set_org_role(self, user_id: int, role: str) -> None:
        self._request("PATCH", f"/api/org/users/{user_id}", json={"role": role})

    def ensure_proxy_user(self, login: str, *, admin: bool) -> None:
        """Crea l'utente come lo farebbe Caddy (auth proxy), senza Bearer:
        serve a metterlo nel team prima del suo primo accesso a /grafana."""
        headers = {"X-WEBAUTH-USER": login}
        if admin:
            headers["X-WEBAUTH-ROLE"] = "Admin"
        self._request("GET", "/api/user", auth=False, headers=headers)

    def get_dashboard(self, uid: str) -> Optional[Mapping[str, Any]]:
        """``{dashboard, folder_uid}`` o None se non esiste."""
        found = self._request("GET", f"/api/dashboards/uid/{uid}", ok404=True)
        if found is None:
            return None
        meta = found.get("meta") or {}
        return {"dashboard": found.get("dashboard") or {}, "folder_uid": meta.get("folderUid")}

    def upsert_dashboard(self, dashboard: Mapping[str, Any], *, folder_uid: str, message: str) -> Mapping[str, Any]:
        return self._request("POST", "/api/dashboards/db", json={
            "dashboard": dict(dashboard), "folderUid": folder_uid, "overwrite": True, "message": message,
        })

    def delete_dashboard(self, uid: str) -> bool:
        return self._request("DELETE", f"/api/dashboards/uid/{uid}", ok404=True) is not None


ClientFactory = Callable[[], GrafanaApi]
_factory_lock = threading.Lock()
_factory: Optional[ClientFactory] = None


def _default_client() -> GrafanaApi:
    token = grafana_settings.service_token()
    base = grafana_settings.api_url()
    if not token or not base:
        raise GrafanaUnavailable("Grafana non configurato (GRAFANA_URL / GRAFANA_SERVICE_TOKEN)")
    return HttpGrafanaClient(base, token, timeout=grafana_settings.timeout_seconds())


def set_client_factory(factory: Optional[ClientFactory]) -> None:
    """Test: sostituisce il client (None = client reale)."""
    global _factory
    with _factory_lock:
        _factory = factory


def get_client() -> GrafanaApi:
    """L'unico modo di ottenere un client: rifiuta se la funzione e' spenta."""
    if not grafana_settings.enabled():
        raise GrafanaDisabled("Dashboard Grafana disabilitate (GRAFANA_ENABLED=false)")
    with _factory_lock:
        factory = _factory or _default_client
    return factory()


__all__ = [
    "GrafanaApi",
    "GrafanaDisabled",
    "GrafanaError",
    "GrafanaUnavailable",
    "HttpGrafanaClient",
    "PERMISSION_EDIT",
    "PERMISSION_VIEW",
    "get_client",
    "set_client_factory",
]
