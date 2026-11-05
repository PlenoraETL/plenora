"""Credenziali delle scritture CARICA REST per riferimento a un connettore API.

Audit frontend R10.8: i segreti non stanno nel canvas. Il nodo CARICA REST
porta solo ``rest_auth_api_id`` (id di un connettore API); al momento della
scrittura il server legge l'autenticazione cifrata del connettore e la
trasforma in intestazioni segrete (mai restituite ne' scritte nei log:
``RestSinkHeader.secret``).

REGOLA UNICA (Fase 5, correzione dopo la conferma T1+T3). Le credenziali
di un connettore partono solo se TUTTE queste condizioni valgono; altrimenti
``RestAuthUnavailable`` con un ``reason`` dichiarato e nessuna richiesta:

| reason              | condizione                                                   |
|---------------------|--------------------------------------------------------------|
| actor_unknown       | non si sa chi usa il connettore (utente o proprietario)       |
| role_not_allowed    | l'utente e' esecutore: non puo' usare i connettori API        |
|                     | (stessa regola di GET /api-connections: require_not_esecutore)|
| not_visible         | il connettore non e' visibile al gruppo dell'UTENTE oppure al |
|                     | gruppo del PROGETTO (proprio o condiviso; superadmin: solo il |
|                     | controllo sul progetto)                                       |
| not_found           | il connettore non esiste piu'                                 |
| connector_no_origin | il connettore non ha un URL da cui derivare l'origine         |
| other_origin        | l'URL della scrittura non ha la stessa origine (schema, host, |
|                     | porta) dell'URL del connettore                                |
| auth_not_header     | il tipo di autenticazione non produce un'intestazione         |
|                     | (none, api_key in query, arcgis_token, tipo sconosciuto)      |
| credential_missing  | il tipo la produrrebbe ma la credenziale e' vuota o il token  |
|                     | non si ottiene (fail-closed: mai una scrittura senza          |
|                     | credenziali quando un connettore e' stato scelto)             |

Chi e' l'utente (``RestAuthActor``):
  - esecuzione lanciata da una persona: l'utente che lancia (stato LIVE dal
    DB al momento della scrittura, non quello del submit);
  - esecuzione non presidiata (catena da schedule/cron, ``user_id`` assente):
    il PROPRIETARIO del progetto (``projects.created_by``). Un progetto senza
    proprietario non puo' usare un connettore in modo non presidiato
    (``actor_unknown``). Lo scheduler diretto (execution_manager) scrive solo
    su DB e non passa da qui.
  - salvataggio del canvas e POST /tasks: l'utente della richiesta.

Canvas salvati prima del riferimento: se il nodo non ha ``rest_auth_api_id``
non c'e' nulla da risolvere e le intestazioni del canvas (anche quelle
segrete) partono come prima: nessuna esecuzione esistente cambia.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from app.core.arricchisci.api_connector import api_connection_service
from app.core.auth.async_user_recheck import load_live_user
from app.core.auth.visibility import is_resource_visible
from app.core.connections.rest_sink import RestSinkConfig, RestSinkHeader
from app.infrastructure.security.crypto import CredentialUnavailableError
from app.shared.connections.oauth import prepare_headers


REST_AUTH_FIELD = "rest_auth_api_id"
# Intestazioni di servizio aggiunte da prepare_headers: non sono credenziali
# e non devono sostituire quelle scelte nel nodo.
_SERVICE_HEADERS = {"accept", "user-agent"}

# Motivi dichiarati del rifiuto (vedi tabella nel docstring del modulo).
REST_AUTH_REASONS = (
    "actor_unknown",
    "role_not_allowed",
    "not_visible",
    "not_found",
    "connector_no_origin",
    "other_origin",
    "auth_not_header",
    "credential_missing",
)

# Tipo di autenticazione del connettore -> intestazione che porta la
# credenziale in una scrittura REST. ``None`` = il tipo non produce
# intestazioni e il connettore non si puo' usare per una scrittura REST.
# ``api_key`` dipende da dove va la chiave (vedi credential_header_name).
# Ogni tipo gestito da shared.connections.oauth.prepare_headers deve essere
# qui (test di enumerazione).
CREDENTIAL_HEADER_BY_AUTH_TYPE: Mapping[str, Optional[str]] = {
    "none": None,
    "bearer": "Authorization",
    "token": "Authorization",
    "oauth2_password": "Authorization",
    "oauth2_client_credentials": "Authorization",
    "api_key": "X-API-Key",
    "basic_auth": "Authorization",
    "basic": "Authorization",
    "arcgis_token": None,
}

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Errori di chi decifra o ottiene la credenziale (segreto non ricostruibile,
# token OAuth non ottenibile, rete): diventano credential_missing.
_CREDENTIAL_ERRORS = (CredentialUnavailableError, ValueError, RuntimeError, OSError)


class RestAuthUnavailable(ValueError):
    """Il connettore di autenticazione non si puo' usare per questa scrittura."""

    def __init__(self, message: str, *, reason: str) -> None:
        if reason not in REST_AUTH_REASONS:
            raise ValueError(f"reason non dichiarato: {reason}")
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RestAuthActor:
    """Chi usa il connettore: utente che lancia/salva o proprietario."""

    user_id: str
    group_id: str
    role: str


def actor_from_user(user: object) -> Optional[RestAuthActor]:
    """RestAuthActor da un CurrentUser (o simile); None se manca l'id."""
    user_id = str(getattr(user, "user_id", "") or "").strip()
    if not user_id:
        return None
    return RestAuthActor(
        user_id=user_id,
        group_id=str(getattr(user, "group_id", "") or ""),
        role=str(getattr(user, "role", "") or ""),
    )


def resolve_rest_auth_actor(
    user_id: Optional[str],
    project_owner_id: Optional[str],
    *,
    load_user: Callable[[Optional[str]], object] = load_live_user,
) -> Optional[RestAuthActor]:
    """Chi usa il connettore: l'utente che lancia, altrimenti il proprietario.

    Esecuzione con persona (``user_id``): quell'utente, LIVE. Non presidiata
    (``user_id`` assente): il proprietario del progetto. Utente disattivato o
    assente: None (``actor_unknown``).
    """
    chosen = user_id if str(user_id or "").strip() else project_owner_id
    if not str(chosen or "").strip():
        return None
    return actor_from_user(load_user(str(chosen)))


def credential_header_name(auth: Mapping[str, object]) -> Optional[str]:
    """Intestazione della credenziale per questo auth (None = nessuna)."""
    auth_type = str(auth.get("type") or "none")
    if auth_type == "api_key":
        location = str(auth.get("location") or auth.get("in") or "header")
        if location != "header":
            return None
        return str(auth.get("key_name") or "X-API-Key")
    return CREDENTIAL_HEADER_BY_AUTH_TYPE.get(auth_type)


def url_origin(url: object) -> Optional[tuple[str, str, int]]:
    """(schema, host, porta) di un URL http/https assoluto, altrimenti None."""
    try:
        parts = urlsplit(str(url or "").strip())
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host or port is None:
        return None
    return scheme, host, int(port)


def connector_url(config: Mapping[str, object]) -> str:
    """URL di riferimento del connettore (endpoint.url, poi base_url, poi url)."""
    endpoint = config.get("endpoint")
    if isinstance(endpoint, Mapping) and endpoint.get("url"):
        return str(endpoint.get("url"))
    for key in ("base_url", "url"):
        if config.get(key):
            return str(config.get(key))
    return ""


def _default_loader(api_id: str) -> Optional[Mapping[str, object]]:
    return api_connection_service.get_api_connection(api_id, include_token=True, strict=True)


def _default_visible(api_id: str, group_id: str) -> bool:
    return bool(is_resource_visible("api_connections", api_id, group_id, is_superadmin=False))


def _default_prepare(auth: Mapping[str, object], custom: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return prepare_headers(dict(auth), dict(custom) if custom else None)


def _masked_loader(api_id: str) -> Optional[Mapping[str, object]]:
    # Per i controlli prima della scrittura bastano tipo di auth e URL:
    # nessun segreto decifrato.
    return api_connection_service.get_api_connection(api_id)


def _connector_auth(config: Mapping[str, object]) -> Mapping[str, object]:
    auth = config.get("auth")
    return auth if isinstance(auth, Mapping) else {}


def _require_header_auth(auth: Mapping[str, object]) -> str:
    expected = credential_header_name(auth)
    if expected is None:
        raise RestAuthUnavailable(
            "L'autenticazione del connettore API non produce un'intestazione "
            f"({str(auth.get('type') or 'none')}): scegli un connettore con credenziali in intestazione",
            reason="auth_not_header",
        )
    return expected


def rest_auth_api_id(carica_node_data: Mapping[str, object]) -> str:
    return str(carica_node_data.get(REST_AUTH_FIELD) or "").strip()


def authorize_rest_auth(
    api_id: str,
    *,
    actor: Optional[RestAuthActor],
    project_group_id: str,
    target_url: str,
    load_connection: Callable[[str], Optional[Mapping[str, object]]] = _default_loader,
    visible: Callable[[str, str], bool] = _default_visible,
) -> Mapping[str, object]:
    """Applica la regola unica e ritorna la configurazione del connettore."""
    if actor is None or not str(actor.user_id or "").strip():
        raise RestAuthUnavailable(
            "Non e' possibile stabilire chi usa il connettore API: "
            "il progetto non ha un proprietario per le esecuzioni non presidiate",
            reason="actor_unknown",
        )
    if actor.role == "esecutore":
        raise RestAuthUnavailable(
            "Il ruolo esecutore non puo' usare i connettori API",
            reason="role_not_allowed",
        )
    group = str(project_group_id or "").strip()
    if not group or not visible(api_id, group):
        raise RestAuthUnavailable(
            "Connettore API di autenticazione non visibile al gruppo del progetto",
            reason="not_visible",
        )
    if actor.role != "superadmin" and (
        not str(actor.group_id or "").strip() or not visible(api_id, actor.group_id)
    ):
        raise RestAuthUnavailable(
            "Connettore API di autenticazione non visibile al tuo gruppo",
            reason="not_visible",
        )
    try:
        config = load_connection(api_id)
    except _CREDENTIAL_ERRORS as exc:
        raise RestAuthUnavailable(
            "Credenziale del connettore API non disponibile",
            reason="credential_missing",
        ) from exc
    if not config:
        raise RestAuthUnavailable("Connettore API di autenticazione non trovato", reason="not_found")
    connector_origin = url_origin(connector_url(config))
    if connector_origin is None:
        raise RestAuthUnavailable(
            "Il connettore API non ha un URL valido: non si puo' stabilire dove mandare le credenziali",
            reason="connector_no_origin",
        )
    if url_origin(target_url) != connector_origin:
        scheme, host, port = connector_origin
        raise RestAuthUnavailable(
            "Le credenziali del connettore si mandano solo a "
            f"{scheme}://{host}:{port}: l'endpoint REST ha un'altra origine",
            reason="other_origin",
        )
    return config


def check_rest_auth_reference(
    carica_node_data: Mapping[str, object],
    *,
    actor: Optional[RestAuthActor],
    project_group_id: str,
    target_url: str,
    load_connection: Callable[[str], Optional[Mapping[str, object]]] = _masked_loader,
    visible: Callable[[str, str], bool] = _default_visible,
) -> None:
    """Controllo prima della scrittura (salvataggio canvas, POST /tasks).

    Stessa regola della scrittura, senza decifrare: utente, visibilita',
    origine, tipo di autenticazione. La credenziale vera si verifica alla
    scrittura (credential_missing).
    """
    api_id = rest_auth_api_id(carica_node_data)
    if not api_id:
        return
    config = authorize_rest_auth(
        api_id,
        actor=actor,
        project_group_id=project_group_id,
        target_url=target_url,
        load_connection=load_connection,
        visible=visible,
    )
    _require_header_auth(_connector_auth(config))


def resolve_rest_auth_headers(
    carica_node_data: Mapping[str, object],
    *,
    actor: Optional[RestAuthActor],
    project_group_id: str,
    target_url: str,
    load_connection: Callable[[str], Optional[Mapping[str, object]]] = _default_loader,
    visible: Callable[[str, str], bool] = _default_visible,
    prepare: Callable[[Mapping[str, object], Optional[Mapping[str, str]]], Mapping[str, str]] = _default_prepare,
) -> tuple[RestSinkHeader, ...]:
    """Intestazioni segrete del connettore indicato dal nodo (vuote se nessuno)."""
    api_id = rest_auth_api_id(carica_node_data)
    if not api_id:
        return ()
    config = authorize_rest_auth(
        api_id,
        actor=actor,
        project_group_id=project_group_id,
        target_url=target_url,
        load_connection=load_connection,
        visible=visible,
    )
    auth = _connector_auth(config)
    expected = _require_header_auth(auth)
    custom = config.get("custom_headers") if isinstance(config.get("custom_headers"), Mapping) else None
    try:
        headers = prepare(auth, custom)
    except _CREDENTIAL_ERRORS as exc:
        raise RestAuthUnavailable(
            "Credenziale del connettore API non disponibile",
            reason="credential_missing",
        ) from exc
    value = next((v for k, v in headers.items() if str(k).lower() == expected.lower()), None)
    basic_without_user = str(auth.get("type")) in ("basic", "basic_auth") and not auth.get("username")
    if value in (None, "") or str(value).strip() in ("Bearer", "Basic") or basic_without_user:
        raise RestAuthUnavailable(
            "Credenziale del connettore API vuota o non ottenibile",
            reason="credential_missing",
        )
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
