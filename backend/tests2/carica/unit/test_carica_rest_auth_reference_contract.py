"""Credenziali CARICA REST per riferimento al connettore API (audit frontend R10.8).

Regola unica (carica/rest_auth.py, Fase 5 correzione dopo la conferma T1+T3):
le credenziali del connettore partono solo se chi usa il connettore puo'
usarlo (utente che lancia/salva; proprietario per le esecuzioni non
presidiate), il connettore e' visibile anche al gruppo del progetto,
l'endpoint ha la STESSA origine dell'URL del connettore e l'autenticazione
produce davvero un'intestazione con una credenziale non vuota. Ogni altro
caso: RestAuthUnavailable con un motivo dichiarato e nessuna richiesta.
"""

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi import HTTPException

from app.core.carica import dispatch, rest_auth
from app.core.carica.rest_auth import (
    CREDENTIAL_HEADER_BY_AUTH_TYPE,
    REST_AUTH_REASONS,
    RestAuthActor,
    RestAuthUnavailable,
    check_rest_auth_reference,
    resolve_rest_auth_actor,
    resolve_rest_auth_headers,
    url_origin,
    with_auth_headers,
)
from app.core.carica.rest_config_parser import rest_target_url
from app.core.connections.rest_sink import RestSinkConfig, RestSinkHeader, RestSinkHttpResponse
from app.core.orchestrator import carica_payload_validation
from app.shared.connections import oauth


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]

URL = "https://api.example.com/items"
CONNECTOR = {
    "id": "api-1",
    "group_id": "g1",
    "endpoint": {"url": "https://api.example.com/v1/base"},
    "auth": {"type": "bearer", "token": "tok-123"},
}
USER = RestAuthActor(user_id="u1", group_id="g1", role="user")


def _resolve(node=None, *, actor=USER, group="g1", target=URL, visible_to=("g1",), connector=CONNECTOR, prepare=None):
    kwargs = {}
    if prepare is not None:
        kwargs["prepare"] = prepare
    return resolve_rest_auth_headers(
        node if node is not None else {"rest_auth_api_id": "api-1"},
        actor=actor,
        project_group_id=group,
        target_url=target,
        load_connection=lambda api_id: connector,
        visible=lambda api_id, group_id: group_id in visible_to,
        **kwargs,
    )


def _reason(**kwargs):
    with pytest.raises(RestAuthUnavailable) as exc:
        _resolve(**kwargs)
    return exc.value.reason


def test_no_reference_means_no_auth_headers():
    assert _resolve({"dest_type": "rest"}) == ()


def test_same_origin_reference_becomes_secret_authorization_header():
    assert _resolve() == (RestSinkHeader(name="Authorization", value="Bearer tok-123", secret=True),)
    # La porta di default esplicita e' la stessa origine.
    assert _resolve(target="https://API.example.com:443/other/path?x=1")


@pytest.mark.parametrize("target", [
    "https://evil.example.net/items",          # altro host
    "http://api.example.com/items",            # altro schema
    "https://api.example.com:8443/items",      # altra porta
    "https://api.example.com.evil.net/items",  # suffisso ingannevole
    "",                                        # nessun URL
])
def test_other_origin_is_refused(target):
    assert _reason(target=target) == "other_origin"


def test_connector_without_url_is_refused():
    assert _reason(connector={**CONNECTOR, "endpoint": {}}) == "connector_no_origin"


def test_url_origin_normalizes_scheme_host_and_default_port():
    assert url_origin("HTTPS://Api.Example.com/x") == ("https", "api.example.com", 443)
    assert url_origin("http://h:8080") == ("http", "h", 8080)
    assert url_origin("ftp://h") is None


def test_connector_must_be_usable_by_the_actor_and_visible_to_the_project():
    # Visibile al progetto ma non all'utente che lancia.
    assert _reason(actor=RestAuthActor("u2", "g2", "user")) == "not_visible"
    # Visibile all'utente ma non al gruppo del progetto.
    assert _reason(group="g3", visible_to=("g1",)) == "not_visible"
    assert _reason(group="") == "not_visible"
    # Esecutore: non usa i connettori (come GET /api-connections).
    assert _reason(actor=RestAuthActor("u3", "g1", "esecutore")) == "role_not_allowed"
    # Nessuno a cui attribuire l'uso (esecuzione non presidiata senza proprietario).
    assert _reason(actor=None) == "actor_unknown"
    # Superadmin: niente controllo sul suo gruppo, resta quello sul progetto.
    assert _resolve(actor=RestAuthActor("s", "staff", "superadmin"))
    assert _reason(actor=RestAuthActor("s", "staff", "superadmin"), group="g9") == "not_visible"
    assert _reason(connector=None) == "not_found"


# Ogni tipo di autenticazione: o produce un'intestazione con credenziale, o
# la scrittura e' rifiutata (fail-closed, mai una scrittura senza credenziali).
AUTH_CASES = [
    ({"type": "none"}, "auth_not_header"),
    ({"type": "bearer", "token": "t"}, None),
    ({"type": "bearer", "token": ""}, "credential_missing"),
    ({"type": "token", "access_token": "t"}, None),
    ({"type": "oauth2_password"}, "oauth"),
    ({"type": "oauth2_client_credentials"}, "oauth"),
    ({"type": "api_key", "location": "header", "key_name": "X-Key", "key_value": "k"}, None),
    ({"type": "api_key", "location": "header", "key_name": "X-Key", "key_value": ""}, "credential_missing"),
    ({"type": "api_key", "location": "query", "key_name": "k", "key_value": "v"}, "auth_not_header"),
    ({"type": "basic_auth", "username": "u", "password": "p"}, None),
    ({"type": "basic", "username": "", "password": ""}, "credential_missing"),
    ({"type": "arcgis_token", "username": "u", "password": "p"}, "auth_not_header"),
    ({"type": "sconosciuto"}, "auth_not_header"),
]


@pytest.mark.parametrize("auth,expected", AUTH_CASES, ids=[f"{c[0]['type']}-{c[1]}" for c in AUTH_CASES])
def test_every_auth_type_is_header_or_refused(auth, expected):
    connector = {**CONNECTOR, "auth": auth}
    if expected == "oauth":
        ok = _resolve(connector=connector, prepare=lambda a, c: oauth.prepare_headers(dict(a), c, oauth2_token_getter=lambda _a: "tok"))
        assert ok and ok[-1].name == "Authorization"
        with pytest.raises(RestAuthUnavailable) as exc:
            _resolve(connector=connector, prepare=lambda a, c: oauth.prepare_headers(dict(a), c, oauth2_token_getter=lambda _a: None))
        assert exc.value.reason == "credential_missing"
        return
    if expected is None:
        headers = _resolve(connector=connector)
        assert headers and all(h.secret for h in headers)
    else:
        assert _reason(connector=connector) == expected


def _auth_types_in_prepare_headers() -> set[str]:
    """Tipi di auth confrontati in oauth.prepare_headers (auth_type == / in)."""
    tree = ast.parse(inspect.getsource(oauth.prepare_headers))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "auth_type":
            for comparator in node.comparators:
                for const in ast.walk(comparator):
                    if isinstance(const, ast.Constant) and isinstance(const.value, str):
                        found.add(const.value)
    return found


def test_credential_table_covers_every_auth_type_of_prepare_headers():
    types = _auth_types_in_prepare_headers()
    assert types, "nessun tipo trovato in prepare_headers"
    assert types <= set(CREDENTIAL_HEADER_BY_AUTH_TYPE), sorted(types - set(CREDENTIAL_HEADER_BY_AUTH_TYPE))
    assert {c[0]["type"] for c in AUTH_CASES} >= set(CREDENTIAL_HEADER_BY_AUTH_TYPE)


def test_reasons_are_declared():
    with pytest.raises(ValueError):
        RestAuthUnavailable("x", reason="inventato")
    assert len(set(REST_AUTH_REASONS)) == len(REST_AUTH_REASONS)


def test_actor_is_the_launching_user_else_the_project_owner():
    users = {"u1": SimpleNamespace(user_id="u1", group_id="g1", role="user"),
             "owner": SimpleNamespace(user_id="owner", group_id="g1", role="admin")}
    load = lambda uid: users.get(uid)  # noqa: E731
    assert resolve_rest_auth_actor("u1", "owner", load_user=load).user_id == "u1"
    # Non presidiata (schedule/catena senza utente): il proprietario del progetto.
    assert resolve_rest_auth_actor(None, "owner", load_user=load).user_id == "owner"
    assert resolve_rest_auth_actor(None, None, load_user=load) is None
    # Utente disattivato/rimosso: nessuno (load_live_user ritorna None).
    assert resolve_rest_auth_actor("gone", "owner", load_user=load) is None


def test_connector_headers_replace_node_headers_with_same_name():
    config = RestSinkConfig(
        url=URL,
        headers=(RestSinkHeader(name="authorization", value="vecchio"), RestSinkHeader(name="X-Client", value="p")),
    )
    merged = with_auth_headers(config, (RestSinkHeader(name="Authorization", value="Bearer t", secret=True),))
    assert [(h.name, h.value, h.secret) for h in merged.headers] == [
        ("X-Client", "p", False),
        ("Authorization", "Bearer t", True),
    ]


# --- scrittura (dispatch) ---------------------------------------------------

def _fake_client(sent):
    def request(**kwargs):
        sent.append(kwargs)
        return RestSinkHttpResponse(status_code=201, body={}, text="")
    return SimpleNamespace(request=MagicMock(side_effect=request))


@pytest.fixture
def wired(monkeypatch):
    """Dispatch con connettore, visibilita' e utenti finti (default reali di rest_auth)."""
    sent: list[dict] = []
    client = _fake_client(sent)
    monkeypatch.setattr(dispatch, "create_default_rest_http_client", lambda: client)
    state = {"connector": CONNECTOR, "visible": {"g1"}, "users": {
        "u1": SimpleNamespace(user_id="u1", group_id="g1", role="user"),
        "u2": SimpleNamespace(user_id="u2", group_id="g2", role="user"),
        "owner": SimpleNamespace(user_id="owner", group_id="g1", role="admin"),
    }, "owner": "owner"}
    monkeypatch.setattr(rest_auth, "api_connection_service", SimpleNamespace(
        get_api_connection=lambda api_id, include_token=False, strict=False: state["connector"],
    ))
    monkeypatch.setattr(rest_auth, "is_resource_visible",
                        lambda table, api_id, group_id, is_superadmin=False: group_id in state["visible"])
    # resolve_rest_auth_actor ha load_live_user come default legato alla definizione.
    monkeypatch.setattr(dispatch, "resolve_rest_auth_actor",
                        lambda user_id, owner_id: rest_auth.resolve_rest_auth_actor(
                            user_id, owner_id, load_user=lambda uid: state["users"].get(uid)))
    monkeypatch.setattr(dispatch, "_get_project_service", lambda: SimpleNamespace(
        get_project_metadata=lambda pid: SimpleNamespace(group_id="g1", created_by=state["owner"]),
    ))
    return SimpleNamespace(sent=sent, client=client, state=state)


def _write(node, user_id="u1"):
    df = pd.DataFrame([{"id": 1}])
    return dispatch.execute_rest_write(
        df, {"dest_type": "rest", "rest_success_statuses": [201], **node}, "p1", "t1",
        progress=lambda *a: None, expected_group_id="g1", user_id=user_id,
    )


def test_rest_write_same_origin_sends_connector_credentials(wired):
    result = _write({"rest_url": URL, "rest_auth_api_id": "api-1"})
    assert result.get("success") is True
    assert wired.sent[0]["headers"]["Authorization"] == "Bearer tok-123"
    assert "tok-123" not in repr(result)


def test_rest_write_other_origin_sends_nothing(wired):
    result = _write({"rest_url": "https://example.org/collect", "rest_auth_api_id": "api-1"})
    assert result["success"] is False
    assert result["error"] == "carica_rest_auth_unavailable"
    assert result["reason"] == "other_origin"
    wired.client.request.assert_not_called()


def test_rest_write_connector_not_usable_by_launching_user_sends_nothing(wired):
    result = _write({"rest_url": URL, "rest_auth_api_id": "api-1"}, user_id="u2")
    assert result["reason"] == "not_visible"
    wired.client.request.assert_not_called()


def test_unattended_write_uses_project_owner(wired):
    assert _write({"rest_url": URL, "rest_auth_api_id": "api-1"}, user_id=None)["success"] is True
    wired.state["owner"] = None
    result = _write({"rest_url": URL, "rest_auth_api_id": "api-1"}, user_id=None)
    assert result["reason"] == "actor_unknown"


@pytest.mark.parametrize("auth", [{"type": "none"}, {"type": "api_key", "location": "query", "key_value": "v"}, {"type": "arcgis_token"}])
def test_rest_write_with_connector_without_header_credential_sends_nothing(wired, auth):
    wired.state["connector"] = {**CONNECTOR, "auth": auth}
    result = _write({"rest_url": URL, "rest_auth_api_id": "api-1"})
    assert result["reason"] == "auth_not_header"
    wired.client.request.assert_not_called()


def test_legacy_canvas_with_secret_headers_and_no_reference_writes_as_before(wired):
    """Canvas salvato prima del riferimento: le sue intestazioni partono come prima."""
    result = _write({
        "rest_url": URL,
        "rest_headers": [{"name": "Authorization", "value": "Bearer legacy", "secret": True},
                         {"name": "X-Client", "value": "plenora"}],
    }, user_id=None)
    assert result["success"] is True
    assert wired.sent[0]["headers"]["Authorization"] == "Bearer legacy"
    assert wired.sent[0]["headers"]["X-Client"] == "plenora"


# --- prima della scrittura: POST /tasks e salvataggio del canvas ------------

def test_http_precheck_maps_reasons_to_typed_errors(wired):
    user = SimpleNamespace(user_id="u2", group_id="g2", role="user")
    node = {"id": "c1", "dest_type": "rest", "rest_url": URL, "rest_auth_api_id": "api-1"}
    with pytest.raises(HTTPException) as exc:
        carica_payload_validation.assert_rest_auth_usable(node, user, project_group_id="g1")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "carica_rest_auth_unavailable"
    assert exc.value.detail["details"]["reason"] == "not_visible"

    owner = SimpleNamespace(user_id="u1", group_id="g1", role="user")
    with pytest.raises(HTTPException) as exc:
        carica_payload_validation.assert_canvas_rest_auth_usable(
            [{"id": "s", "type": "source"}, {**node, "rest_url": "https://other.test/x"}], owner, project_group_id="g1")
    assert exc.value.status_code == 400
    assert exc.value.detail["details"]["reason"] == "other_origin"

    # Stessa origine e utente che puo' usarlo: nessun errore; senza riferimento nulla da controllare.
    carica_payload_validation.assert_canvas_rest_auth_usable([node], owner, project_group_id="g1")
    carica_payload_validation.assert_rest_auth_usable({"dest_type": "rest", "rest_url": "https://x.test"}, user, project_group_id="g1")


def test_precheck_reads_the_same_url_as_the_write():
    nested = {"dest_type": "rest", "rest_auth_api_id": "api-1", "rest_config": {"url": "https://evil.test/x"}}
    with pytest.raises(RestAuthUnavailable) as exc:
        check_rest_auth_reference(
            nested, actor=USER, project_group_id="g1",
            target_url=rest_target_url(nested),
            load_connection=lambda _id: CONNECTOR, visible=lambda *_a: True,
        )
    assert exc.value.reason == "other_origin"
    assert rest_target_url(nested) == "https://evil.test/x"


def test_precheck_is_wired_into_task_creation_and_canvas_save():
    backend = Path(__file__).resolve().parents[3]
    orchestrator = (backend / "app/core/orchestrator/router.py").read_text(encoding="utf-8")
    projects = (backend / "app/core/projects/router.py").read_text(encoding="utf-8")
    assert "assert_rest_auth_usable(" in orchestrator.split("def create_task", 1)[1].split("\n@", 1)[0]
    assert "assert_canvas_rest_auth_usable(" in projects.split("def update_project_canvas", 1)[1].split("\n@", 1)[0]
