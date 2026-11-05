"""Credenziali delle scritture CARICA REST per riferimento a un connettore API.

Audit frontend R10.8: i segreti non stanno nel canvas. Il nodo CARICA REST
porta solo ``rest_auth_api_id`` (id di un connettore API del gruppo, o
condiviso con esso); al momento della scrittura il server legge
l'autenticazione cifrata del connettore e la trasforma in intestazioni
segrete (mai restituite ne' scritte nei log: ``RestSinkHeader.secret``).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping, Optional

from app.core.arricchisci.api_connector import api_connection_service
from app.core.auth.visibility import is_resource_visible
from app.core.connections.rest_sink import RestSinkConfig, RestSinkHeader
from app.shared.connections.oauth import prepare_headers


REST_AUTH_FIELD = "rest_auth_api_id"
# Intestazioni di servizio aggiunte da prepare_headers: non sono credenziali
# e non devono sostituire quelle scelte nel nodo.
_SERVICE_HEADERS = {"accept", "user-agent"}


class RestAuthUnavailable(ValueError):
    """Il connettore di autenticazione non e' utilizzabile per questo progetto."""


def _default_loader(api_id: str) -> Optional[Mapping[str, object]]:
    return api_connection_service.get_api_connection(api_id, include_token=True, strict=True)


def _default_visible(api_id: str, group_id: str) -> bool:
    return bool(is_resource_visible("api_connections", api_id, group_id, is_superadmin=False))


def _default_prepare(auth: Mapping[str, object], custom: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return prepare_headers(dict(auth), dict(custom) if custom else None)


def resolve_rest_auth_headers(
    carica_node_data: Mapping[str, object],
    group_id: str,
    *,
    load_connection: Callable[[str], Optional[Mapping[str, object]]] = _default_loader,
    visible: Callable[[str, str], bool] = _default_visible,
    prepare: Callable[[Mapping[str, object], Optional[Mapping[str, str]]], Mapping[str, str]] = _default_prepare,
) -> tuple[RestSinkHeader, ...]:
    """Intestazioni segrete del connettore indicato dal nodo (vuote se nessuno)."""
    api_id = str(carica_node_data.get(REST_AUTH_FIELD) or "").strip()
    if not api_id:
        return ()
    group = str(group_id or "").strip()
    # Fail-closed: il connettore deve essere visibile al gruppo del PROGETTO.
    if not group or not visible(api_id, group):
        raise RestAuthUnavailable("Connettore API di autenticazione non visibile al gruppo del progetto")
    config = load_connection(api_id)
    if not config:
        raise RestAuthUnavailable("Connettore API di autenticazione non trovato")
    auth = config.get("auth") if isinstance(config.get("auth"), Mapping) else {}
    custom = config.get("custom_headers") if isinstance(config.get("custom_headers"), Mapping) else None
    headers = prepare(auth or {}, custom)
    return tuple(
        RestSinkHeader(name=str(name), value=str(value), secret=True)
        for name, value in headers.items()
        if str(name).lower() not in _SERVICE_HEADERS and value not in (None, "")
    )


def with_auth_headers(config: RestSinkConfig, auth_headers: tuple[RestSinkHeader, ...]) -> RestSinkConfig:
    """Config con le credenziali del connettore (sostituiscono omonime del nodo)."""
    if not auth_headers:
        return config
    names = {header.name.lower() for header in auth_headers}
    kept = tuple(header for header in config.headers if header.name.lower() not in names)
    return replace(config, headers=kept + auth_headers)
