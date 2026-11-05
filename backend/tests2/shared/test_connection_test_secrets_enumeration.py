"""
Esito del «Test connessione»: nessun segreto nel messaggio salvato ne' in
``last_test`` delle liste (Fase 5, voce S3, difesa a due livelli di
``app/shared/connection_tests/results.py`` e ``app/shared/error_sanitizer.py``).

Enumerazione: corpus di messaggi d'errore reali (requests ConnectionError /
SSLError / HTTPError costruiti con le classi vere di requests e urllib3,
corpi JSON/HTML della risposta remota, messaggi di psycopg, oracledb,
pyodbc, paramiko, DSN) x tutti i tipi di autenticazione dei connettori API
e delle connessioni (database, ArcGIS, Data Source). I valori segreti sono
noti e inseriti nel corpus, anche codificati (percent-encoding, Base64 di
``utente:password``): nessuna forma deve comparire.

Tre livelli di verifica:
 1. regola unica di registrazione (decoratore + lista) su tutto il prodotto
    corpus x risorse;
 2. il sanitizer generico DA SOLO (senza conoscere i segreti) sulle forme che
    riconosce per contesto (query string, nome=valore, JSON, DSN, ODBC,
    Oracle, Authorization);
 3. le quattro route reali di test, con il ``secrets_of`` dichiarato sulla
    route, e il percorso reale del test dei connettori API
    (``ApiConnectionService.test_api_connection``: niente corpo grezzo della
    risposta remota, massimo 200 caratteri).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any, Callable, NamedTuple
from urllib.parse import quote, quote_plus

import pytest
import requests
from requests.structures import CaseInsensitiveDict
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import MaxRetryError, NewConnectionError, SSLError as Urllib3SSLError

from app.shared.connection_tests import results
from app.shared.contracts.auth import CurrentUser
from app.shared.error_sanitizer import sanitize_http_detail

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
_HOST = "api.esempio.it"


def _secret(tag: str) -> str:
    """Segreto con i caratteri che cambiano forma in un URL (spazio, @, /, &, =)."""
    return f"Zk7{tag}Qp9 p@ss/w&r=d!"


def _plain(tag: str) -> str:
    """Segreto alfanumerico (forma che il sanitizer generico deve riconoscere
    per contesto senza conoscerlo)."""
    return f"Zk7{tag}Qp9xW2"


def _user() -> CurrentUser:
    return CurrentUser(user_id="u-1", username="mario", group_id="g-1", role="user")


# Risorse: tutti i tipi di autenticazione


class Resource(NamedTuple):
    kind: str
    name: str
    source: Any  # config o modello con i segreti in chiaro
    secrets: tuple[str, ...]  # valori segreti noti
    user: str  # utente (per Basic auth e DSN)


def _api(name: str, auth: dict, *, secrets: tuple[str, ...], custom_headers: dict | None = None, user: str = "") -> Resource:
    config = {
        "endpoint": {"url": f"https://{_HOST}/v1/data", "method": "GET"},
        "auth": auth,
        "custom_headers": custom_headers or {},
        "options": {"timeout": 1},
    }
    return Resource("api_connection", name, config, secrets, user)


def _api_resources(make: Callable[[str], str]) -> list[Resource]:
    out = []
    for key_name in ("key", "appid", "subscription-key", "access_key", "sig", "apiKey", "api_key", "token", "code"):
        s = make(f"q{len(out)}")
        out.append(_api(
            f"api_key_query_{key_name}",
            {"type": "api_key", "location": "query", "key_name": key_name, "key_value": s},
            secrets=(s,),
        ))
    for header in ("X-API-Key", "Ocp-Apim-Subscription-Key", "apikey", "X-Custom"):
        s = make(f"h{len(out)}")
        out.append(_api(
            f"api_key_header_{header}",
            {"type": "api_key", "location": "header", "key_name": header, "key_value": s},
            secrets=(s,),
        ))
    s = make("bearer")
    out.append(_api("bearer", {"type": "bearer", "token": s}, secrets=(s,)))
    s = make("token")
    out.append(_api("token", {"type": "token", "access_token": s}, secrets=(s,)))
    for basic in ("basic", "basic_auth"):
        s = make(basic)
        out.append(_api(basic, {"type": basic, "username": "mario.rossi", "password": s},
                        secrets=(s,), user="mario.rossi"))
    p, cs, rt = make("o2p"), make("o2cs"), make("o2rt")
    out.append(_api(
        "oauth2_password",
        {"type": "oauth2_password", "token_url": f"https://{_HOST}/token", "username": "mario.rossi",
         "password": p, "client_id": "plenora-app", "client_secret": cs, "refresh_token": rt},
        secrets=(p, cs, rt), user="mario.rossi",
    ))
    cs = make("o2cc")
    out.append(_api(
        "oauth2_client_credentials",
        {"type": "oauth2_client_credentials", "token_url": f"https://{_HOST}/token",
         "client_id": "plenora-app", "client_secret": cs},
        secrets=(cs,), user="plenora-app",
    ))
    p = make("agt")
    out.append(_api(
        "arcgis_token",
        {"type": "arcgis_token", "token_url": f"https://{_HOST}/sharing/rest/generateToken",
         "username": "gisuser", "password": p},
        secrets=(p,), user="gisuser",
    ))
    s = make("custom")
    out.append(_api("custom_auth_value", {"type": "custom", "value": s}, secrets=(s,)))
    s = make("hdr")
    out.append(_api("custom_header", {"type": "none"}, secrets=(s,),
                    custom_headers={"X-Auth-Token": s, "Accept": "application/json"}))
    return out


def _db_resources(make: Callable[[str], str]) -> list[Resource]:
    from app.core.connections.models import DatabaseConnection

    out = []
    for db_type in ("postgres", "mssql", "mysql", "oracle"):
        p = make(f"db{db_type}")
        model = DatabaseConnection(
            id=f"c-{db_type}", name=db_type, type=db_type, host="db.interno", port=5432,
            database="erp", username="scott", password=p, created_at=_NOW, updated_at=_NOW,
        )
        out.append(Resource("connection", f"db_{db_type}", model, (p,), "scott"))
    return out


def _arcgis_resources(make: Callable[[str], str]) -> list[Resource]:
    from app.core.connections.arcgis_models import ArcGISConnection

    p, cs = make("agp"), make("agcs")
    token = ArcGISConnection(id="g-1", name="t", server_url=f"https://{_HOST}/arcgis", auth_type="token",
                             username="gisuser", password=p, created_at=_NOW, updated_at=_NOW)
    oauth = ArcGISConnection(id="g-2", name="o", server_url=f"https://{_HOST}/arcgis", auth_type="oauth2",
                             client_id="gis-app", client_secret=cs, created_at=_NOW, updated_at=_NOW)
    return [
        Resource("arcgis_connection", "arcgis_conn_token", token, (p,), "gisuser"),
        Resource("arcgis_connection", "arcgis_conn_oauth2", oauth, (cs,), "gis-app"),
    ]


def _datasource_resources(make: Callable[[str], str]) -> list[Resource]:
    p1, p2, pk, pp, cs = make("sftp"), make("ftp"), make("pkey"), make("pass"), make("spcs")
    pem = f"-----BEGIN OPENSSH PRIVATE KEY-----\nb3Blbn{pk}\n-----END OPENSSH PRIVATE KEY-----"
    return [
        Resource("datasource", "sftp_password",
                 {"id": "d-1", "type": "sftp", "host": "sftp.interno", "username": "etl", "password": p1},
                 (p1,), "etl"),
        Resource("datasource", "sftp_private_key",
                 {"id": "d-2", "type": "sftp", "host": "sftp.interno", "username": "etl",
                  "private_key": pem, "private_key_passphrase": pp},
                 (pem, pp), "etl"),
        Resource("datasource", "ftp",
                 {"id": "d-3", "type": "ftp", "host": "ftp.interno", "username": "etl", "password": p2},
                 (p2,), "etl"),
        Resource("datasource", "sharepoint",
                 {"id": "d-4", "type": "sharepoint", "tenant_id": "t", "client_id": "sp-app", "client_secret": cs},
                 (cs,), "sp-app"),
    ]


def _all_resources(make: Callable[[str], str]) -> list[Resource]:
    return _api_resources(make) + _db_resources(make) + _arcgis_resources(make) + _datasource_resources(make)


RESOURCES = _all_resources(_secret)
assert {r.kind for r in RESOURCES} == set(results.CONNECTION_TEST_KINDS), "un tipo senza risorse nel corpus"


def _forms(secret: str, user: str) -> set[str]:
    """Le forme in cui un segreto puo' comparire in un messaggio."""
    forms = {secret, quote(secret, safe=""), quote_plus(secret), quote(secret)}
    forms.add(base64.b64encode(f"{user}:{secret}".encode()).decode())
    # Il nucleo univoco del segreto (``Zk7<tag>...``): anche un pezzo non deve restare.
    forms.update(re.findall(r"Zk7\w+", secret))
    return {f for f in forms if len(f) >= 6}


def _assert_clean(text: str, resource: Resource, *, context: str) -> None:
    for secret in resource.secrets:
        for form in _forms(secret, resource.user):
            assert form not in text, f"{resource.name} [{context}]: {form!r} in {text!r}"
            if "%" in form:  # percent-encoding minuscolo
                assert form.lower() not in text.lower(), f"{resource.name} [{context}]: {text!r}"


# Corpus di messaggi d'errore reali


def _prepared(url: str, params: dict | None = None, headers: dict | None = None) -> requests.PreparedRequest:
    return requests.Request("GET", url, params=params, headers=headers).prepare()


def _requests_connection_error(path_url: str) -> str:
    pool = HTTPSConnectionPool(_HOST, 443)
    reason = NewConnectionError(pool, "Failed to establish a new connection: [Errno 111] Connection refused")
    exc = requests.exceptions.ConnectionError(MaxRetryError(pool, path_url, reason))
    return f"Errore di connessione: {exc}"


def _requests_ssl_error(path_url: str) -> str:
    pool = HTTPSConnectionPool(_HOST, 443)
    reason = Urllib3SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    exc = requests.exceptions.SSLError(MaxRetryError(pool, path_url, reason))
    return f"Errore di connessione: {exc}"


def _requests_http_error(full_url: str, status: int, body: str) -> str:
    response = requests.Response()
    response.status_code = status
    response.url = full_url
    response.reason = "Unauthorized"
    response._content = body.encode()
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        return f"Errore HTTP: {exc}"
    raise AssertionError("raise_for_status non ha sollevato")


def _corpus(resource: Resource) -> list[tuple[str, str]]:
    """(nome, messaggio) con ogni segreto della risorsa dentro."""
    out = []
    for i, s in enumerate(resource.secrets):
        u = resource.user or "utente"
        b = base64.b64encode(f"{u}:{s}".encode()).decode()
        prepared = _prepared(f"https://{_HOST}/v1/data", params={"q": "roma", "key": s, "appid": s})
        path_url, full_url = prepared.path_url, prepared.url
        messages = {
            "requests_connection_error": _requests_connection_error(path_url),
            # Parametro dal nome qualunque: solo la regola della query string lo copre.
            "requests_unnamed_param": _requests_connection_error(
                _prepared(f"https://{_HOST}/v1/x", params={"q": s}).path_url,
            ),
            "requests_ssl_error": _requests_ssl_error(path_url),
            "requests_http_error": _requests_http_error(full_url, 401, '{"error":"bad key"}'),
            "http_body_json": f'HTTP 401: {{"error":"invalid","api_key":"{s}","password": "{s}"}}',
            "http_body_json_escaped": 'HTTP 400: {\\"token\\": \\"' + s + '\\"}',
            "http_body_html": f"HTTP 403: <html><body><p>Chiave {s} non valida</p></body></html>",
            "http_body_echo_header": f"HTTP 401: received Authorization: Bearer {s}",
            "http_body_basic": f"HTTP 401: received Authorization: Basic {b}",
            "evaluation_error": f"Errore applicativo: credenziale {s} scaduta",
            "quoted_with_spaces": f"login failed password='{s}'",
            "psycopg_dsn": f'invalid connection string "postgresql://{u}:{quote(s, safe="")}@db.interno:5432/erp"',
            "psycopg_fatal": f'connection failed: FATAL:  password authentication failed for user "{u}" ({s})',
            "oracledb": f"DPY-6005: cannot connect to database. {u}/{s}@db.interno:1521/ORCL",
            "pyodbc": (
                "('28000', \"[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
                f"Login failed for user '{u}'. (18456) DRIVER={{ODBC Driver 18}};SERVER=db;UID={u};PWD={{{s}}};\")"
            ),
            "mongodb_dsn": f"ServerSelectionTimeoutError: mongodb://{u}:{quote_plus(s)}@mongo.interno:27017",
            "redis_dsn": f"Error connecting to redis://:{quote_plus(s)}@cache.interno:6379/0",
            "paramiko": f"AuthenticationException: Authentication failed for {u} (passphrase={s})",
            "repr_dict": f"Bad request headers: {{'Authorization': 'Bearer {s}', 'X-Api-Key': {s!r}}}",
        }
        out.extend((f"{name}#{i}", msg) for name, msg in messages.items())
    return out


class _MemoryStore:
    """Lo store reale in memoria: quello che si registra si rilegge."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}

    def record(self, entry):
        self.rows[(entry.kind, entry.resource_id)] = {
            "resource_id": entry.resource_id, "success": entry.success, "message": entry.message,
            "tested_at": entry.tested_at, "tested_by_name": entry.tested_by_name,
            "tested_by_group": "g-1", "duration_ms": entry.duration_ms,
        }

    def reset(self, kind, resource_id, at):
        self.rows.pop((kind, resource_id), None)

    def load(self, kind, resource_ids):
        return [row for (k, rid), row in self.rows.items() if k == kind and rid in resource_ids]


@pytest.fixture
def store(monkeypatch):
    memory = _MemoryStore()
    monkeypatch.setattr(results, "_store", memory)
    return memory


def _id_param(kind: str) -> str:
    return {"connection": "connection_id", "arcgis_connection": "connection_id",
            "datasource": "ds_id", "api_connection": "api_id"}[kind]


_CASES = [(r, name, msg) for r in RESOURCES for name, msg in _corpus(r)]


def test_corpus_covers_every_kind_and_auth_type():
    assert len(_CASES) > 500
    assert {r.kind for r, _, _ in _CASES} == set(results.CONNECTION_TEST_KINDS)
    api_auth = {r.source["auth"]["type"] for r in RESOURCES if r.kind == "api_connection"}
    assert api_auth >= {"api_key", "bearer", "token", "basic", "basic_auth", "oauth2_password",
                        "oauth2_client_credentials", "arcgis_token", "custom", "none"}


@pytest.mark.parametrize("field", ["message", "error"])
@pytest.mark.parametrize(("resource", "case", "message"), _CASES,
                         ids=[f"{r.name}-{c}" for r, c, _ in _CASES])
def test_saved_message_and_list_have_no_secret(store, resource, case, message, field):
    """Livello 1+2: decoratore della route di test -> store -> lista."""
    id_param = _id_param(resource.kind)

    @results.records_connection_test(resource.kind, id_param=id_param, secrets_of=lambda rid: resource.source)
    def test_route(current_user: CurrentUser, **kwargs):
        return {"success": False, field: message}

    @results.attaches_connection_tests(resource.kind, items_field="items")
    def list_route(current_user: CurrentUser):
        return {"items": [{"id": "r-1"}]}

    test_route(current_user=_user(), **{id_param: "r-1"})
    saved = store.rows[(resource.kind, "r-1")]["message"]
    _assert_clean(saved, resource, context=f"{case} salvato")
    assert len(saved) <= results.MESSAGE_MAX_LEN
    last_test = list_route(current_user=_user())["items"][0]["last_test"]
    _assert_clean(str(last_test), resource, context=f"{case} last_test")


# Livello 2 da solo: il sanitizer generico riconosce i segreti per contesto

_GENERIC_ONLY = {
    "requests_connection_error", "requests_unnamed_param", "requests_ssl_error", "requests_http_error",
    "http_body_json",
    "http_body_json_escaped", "http_body_echo_header", "http_body_basic", "quoted_with_spaces",
    "psycopg_dsn", "oracledb", "pyodbc", "mongodb_dsn", "redis_dsn", "paramiko", "repr_dict",
}
_PLAIN_RESOURCES = _all_resources(_plain)
_GENERIC_CASES = [
    (r, name, msg)
    for r in _PLAIN_RESOURCES
    for name, msg in _corpus(r)
    if name.split("#")[0] in _GENERIC_ONLY and "BEGIN OPENSSH" not in msg
]


@pytest.mark.parametrize(("resource", "case", "message"), _GENERIC_CASES,
                         ids=[f"{r.name}-{c}" for r, c, _ in _GENERIC_CASES])
def test_generic_sanitizer_alone_recognizes_context(resource, case, message):
    _assert_clean(sanitize_http_detail(message), resource, context=f"{case} generico")


def test_generic_corpus_is_not_empty():
    assert {name.split("#")[0] for _, name, _ in _GENERIC_CASES} == _GENERIC_ONLY


# Livello 3: le route reali e il percorso reale del test dei connettori API


def _resource(name: str) -> Resource:
    (found,) = [r for r in RESOURCES if r.name == name]
    return found


def _leaky_message(resource: Resource) -> str:
    s = resource.secrets[0]
    return f"Login fallito: {s} / {quote_plus(s)} su host interno"


def test_real_database_route(store, monkeypatch):
    from app.core.connections import router as connections_router
    from app.core.connections.models import ConnectionTestResult

    resource = _resource("db_postgres")

    class _Service:
        def get_connection(self, connection_id):
            return resource.source

        def test_connection(self, connection_id):
            return ConnectionTestResult(success=False, message=_leaky_message(resource))

    monkeypatch.setattr(connections_router, "_connection_service", _Service())
    monkeypatch.setattr(connections_router, "assert_resource_visible", lambda *a, **k: None)
    connections_router.test_connection(connection_id="c-1", current_user=_user())
    _assert_clean(store.rows[("connection", "c-1")]["message"], resource, context="route database")


def test_real_datasource_route(store, monkeypatch):
    from app.core.datasources import router as datasources_router
    from app.core.datasources.models import DataSourceTestResult

    resource = _resource("sftp_password")

    class _Service:
        def get_datasource(self, ds_id):
            return object()

        def _get_full_config(self, ds_id):
            return resource.source

        def test_connection(self, ds_id):
            return DataSourceTestResult(success=False, message=_leaky_message(resource))

    monkeypatch.setattr(datasources_router, "_service", _Service())
    monkeypatch.setattr(datasources_router, "assert_resource_visible", lambda *a, **k: None)
    datasources_router.test_connection(ds_id="d-1", current_user=_user())
    _assert_clean(store.rows[("datasource", "d-1")]["message"], resource, context="route datasource")


def test_real_arcgis_route(store, monkeypatch):
    from app.core.connections.arcgis_models import ArcGISTestResult
    from app.core.features import routes as features_routes

    resource = _resource("arcgis_conn_token")

    class _Service:
        def get_connection(self, connection_id):
            return resource.source

        def test_connection(self, connection_id):
            return ArcGISTestResult(success=False, message=_leaky_message(resource))

    monkeypatch.setattr(features_routes, "_service", _Service())
    monkeypatch.setattr(features_routes, "_check_arcgis_access", lambda *a, **k: None)
    features_routes.test_connection(connection_id="g-1", current_user=_user())
    _assert_clean(store.rows[("arcgis_connection", "g-1")]["message"], resource, context="route arcgis")


def _fake_http(mode: str, resource: Resource):
    """``execute_http_request`` finto: risponde con un corpo che ripete i
    segreti, o solleva i veri errori di requests costruiti dalla richiesta."""
    from app.core.arricchisci.http_executor import NormalizedResponse

    def execute(spec):
        prepared = _prepared(spec.url, params=dict(spec.params or {}), headers=dict(spec.headers or {}))
        if mode == "connection_error":
            raise requests.exceptions.ConnectionError(
                MaxRetryError(HTTPSConnectionPool(_HOST, 443), prepared.path_url,
                              NewConnectionError(None, "Connection refused")),
            )
        if mode == "ssl_error":
            raise requests.exceptions.SSLError(
                MaxRetryError(HTTPSConnectionPool(_HOST, 443), prepared.path_url, Urllib3SSLError("bad cert")),
            )
        echo = " ".join(resource.secrets)
        headers_dump = "; ".join(f"{k}: {v}" for k, v in prepared.headers.items())
        body = (
            f'{{"error":"invalid credentials","received":"{echo}","url":"{prepared.url}",'
            f'"headers":"{headers_dump}","api_key":"{resource.secrets[0]}"}}'
            + " padding" * 100
        )
        raw = requests.Response()
        raw.status_code = 401
        raw._content = body.encode()
        raw.headers = CaseInsensitiveDict({"Content-Type": "application/json"})
        return NormalizedResponse(status_code=401, headers=raw.headers, text=body, elapsed_ms=1.0, raw_response=raw)

    return execute


_API_RESOURCES = [r for r in RESOURCES if r.kind == "api_connection"]


@pytest.mark.parametrize("mode", ["http_401_echo", "connection_error", "ssl_error"])
@pytest.mark.parametrize("resource", _API_RESOURCES, ids=[r.name for r in _API_RESOURCES])
def test_real_api_route_and_service(store, monkeypatch, resource, mode):
    """Route reale ``POST /api-connections/{id}/test`` + servizio reale: il
    motivo restituito (``error``) e quello salvato non hanno segreti ne'
    il corpo grezzo, e il motivo e' lungo al massimo 200 caratteri."""
    from app.core.arricchisci import api_connection_management as management
    from app.core.arricchisci import router as arricchisci_router
    from app.core.arricchisci.api_connector import ApiConnectionService
    from app.shared.connections.oauth import prepare_headers

    service = ApiConnectionService.__new__(ApiConnectionService)
    dynamic_token = "Zk7dynTOKENxx91"
    monkeypatch.setattr(service, "get_api_connection",
                        lambda api_id, include_token=False, strict=False: dict(resource.source), raising=False)
    monkeypatch.setattr(service, "_prepare_headers",
                        lambda auth, custom: prepare_headers(auth, custom, oauth2_token_getter=lambda a: dynamic_token),
                        raising=False)
    monkeypatch.setattr(management, "get_arcgis_token", lambda auth: dynamic_token)
    monkeypatch.setattr(management, "execute_http_request", _fake_http(mode, resource))
    monkeypatch.setattr(arricchisci_router, "_api_connection_service", service)
    monkeypatch.setattr(arricchisci_router, "_check_api_connection_visible", lambda *a, **k: None)

    out = arricchisci_router.test_api_connection(api_id="a-1", body=None, current_user=_user())

    assert out["success"] is False
    error = out["error"]
    assert len(error) <= management.TEST_ERROR_MAX_LEN
    _assert_clean(error, resource, context=f"{mode} risposta")
    assert dynamic_token not in error
    if mode == "http_401_echo":
        assert error.startswith("HTTP 401: ")
        assert "invalid credentials" in error  # estratto, sanitizzato
        assert error.endswith("...[troncato]")  # mai il corpo intero
    saved = store.rows[("api_connection", "a-1")]["message"]
    _assert_clean(saved, resource, context=f"{mode} salvato")
    assert dynamic_token not in saved
