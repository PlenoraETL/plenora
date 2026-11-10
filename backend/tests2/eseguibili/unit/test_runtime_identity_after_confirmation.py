"""Input «Utente che esegue»: correzione dopo la conferma, punti 2, 3 e 4.

2. Identita' = username ESATTO del DB; gli username nuovi senza spazi
   iniziali/finali ne' caratteri di controllo, unici in forma normalizzata
   (trim + casefold) in ogni scrittura (``auth.db.create_user``).
3. Le factory di ``RuntimeIdentity`` non sollevano: ogni blocco nasce nella
   risoluzione, dentro il mappatore degli errori di preparazione. Ogni
   endpoint di anteprima/stima con due username di prova diversi risponde con
   il blocco tipizzato (400 ``runtime_identity_required``; Valida: issue con
   lo stesso codice), mai 500.
4. Un input «Utente che esegue» cambia SOLO quell'input: anteprima, valori
   distinti e Valida trattano gli altri input come senza quel nodo.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import db as auth_db
from app.core.auth.exceptions import UsernameInvalidError, UsernameTakenError
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import apply_identity_inputs
from app.shared import error_codes
from app.shared.contracts.auth import normalized_username, username_form_error
from app.core.eseguibili.identity_transform import identity_values
from app.shared.execution.runtime_identity import (
    RuntimeIdentity,
    RuntimeIdentityError,
)
from tests2.eseguibili.unit.test_runtime_identity_inputs import (
    DEFS,
    MARIO,
    _code,
    _manipola_canvas,
    _project_app,
)
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

REQUIRED = error_codes.RUNTIME_IDENTITY_REQUIRED
POS = {"x": 0, "y": 0}
BACKEND = Path(__file__).resolve().parents[3]
REPO = BACKEND.parent


# ---------------------------------------------------------------- 2. username


@pytest.mark.parametrize("username", ["Mario.Rossi", "mario rossi", "Ünïcode", "x' OR '1'='1"])
def test_identity_is_the_exact_username(username):
    identity = RuntimeIdentity.authenticated(build_current_user(username=username))
    assert identity.username == username
    assert identity_values(DEFS, identity=identity, client_values={}) == {"utente": username}


@pytest.mark.parametrize("username", [" mario.rossi", "mario.rossi ", "mario\tx", "mario\x00", "\u0085mario"])
def test_identity_refuses_a_username_outside_the_rule(username):
    """Utenti creati prima della regola: mai uno username che un database
    potrebbe confrontare uguale a quello di un altro (niente strip)."""
    identity = RuntimeIdentity.authenticated(build_current_user(username=username))
    assert identity.username is None and identity.reason == "username_invalid"
    with pytest.raises(RuntimeIdentityError) as err:
        identity_values(DEFS, identity=identity, client_values={})
    assert err.value.code == REQUIRED and "amministratore" in err.value.message


@pytest.mark.parametrize("username,ok", [
    ("mario.rossi", True), ("Mario Rossi", True), ("", False), ("  ", False), (" mario", False),
    ("mario ", False), ("ma\nrio", False), ("ma\x7frio", False), (None, False),
])
def test_username_form_rule(username, ok):
    assert (username_form_error(username) is None) is ok


def test_normalized_form_is_trim_and_casefold():
    assert normalized_username(" Mario ") == "mario"
    assert normalized_username("Straße") == normalized_username("STRASSE")


class _Users:
    """Tabella utenti in memoria per ``auth.db`` (niente DB)."""

    def __init__(self, monkeypatch, names):
        self.rows = {f"u{i}": name for i, name in enumerate(names)}
        self.inserts: list = []
        monkeypatch.setattr(auth_db, "_fetchall", lambda sql, params=(): [
            {"id": uid, "username": name} for uid, name in self.rows.items()])
        monkeypatch.setattr(auth_db, "get_user", lambda uid: SimpleNamespace(
            id=uid, username=self.rows[uid], email=None, group_id="g", role="user", is_active=True,
            created_at="2026-11-05T00:00:00+00:00") if uid in self.rows else None)

        def execute(sql, params=()):
            self.inserts.append(params)
            self.rows[params[0]] = params[1]

        monkeypatch.setattr(auth_db, "_execute", execute)


@pytest.mark.parametrize("existing,new", [
    ("mario", "Mario"), ("mario", "MARIO"), ("strasse", "Straße"), ("Anna", "anna"),
])
def test_create_user_refuses_a_normalized_collision(existing, new, monkeypatch):
    users = _Users(monkeypatch, [existing])
    with pytest.raises(UsernameTakenError):
        auth_db.create_user(user_id="x", username=new, password_hash="h", group_id="g")
    assert users.inserts == []


@pytest.mark.parametrize("new", [" mario", "mario ", "ma\trio", ""])
def test_create_user_refuses_an_invalid_form(new, monkeypatch):
    users = _Users(monkeypatch, [])
    with pytest.raises(UsernameInvalidError):
        auth_db.create_user(user_id="x", username=new, password_hash="h", group_id="g")
    assert users.inserts == []


def test_create_user_accepts_a_new_username(monkeypatch):
    users = _Users(monkeypatch, ["mario"])
    auth_db.create_user(user_id="x", username="anna", password_hash="h", group_id="g")
    assert users.rows["x"] == "anna"


def _users_client(monkeypatch, names):
    from app.core.auth import users_router as module

    _Users(monkeypatch, names)
    monkeypatch.setattr(module, "hash_password", lambda password: "hash")
    app = FastAPI()
    app.dependency_overrides[module.require_admin] = lambda: build_current_user(role="superadmin")
    app.include_router(module.users_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("username,status,code", [
    ("Mario", 409, error_codes.USERNAME_ALREADY_EXISTS),
    ("mario ", 400, error_codes.VALIDATION_ERROR),
    (" anna", 400, error_codes.VALIDATION_ERROR),
    ("anna", 201, None),
])
def test_user_creation_api_applies_the_rule(username, status, code, monkeypatch):
    client = _users_client(monkeypatch, ["mario"])
    response = client.post("/api/auth/users", json={
        "username": username, "password": "x" * 12, "group_id": "g", "role": "user"})
    assert response.status_code == status, response.text
    if code:
        assert _code(response) == code


def test_every_user_write_goes_through_create_user():
    """Unico INSERT sugli utenti: ``auth.db.create_user`` (API, bootstrap
    degli installer, seed). Nessuna modifica dello username esiste
    (``UserUpdateRequest`` non lo accetta)."""
    from app.core.auth.models import UserUpdateRequest

    inserts = []
    for root in (BACKEND / "app", BACKEND / "workers", BACKEND / "scripts", REPO / "scripts",
                 BACKEND / "test-e2e-v2"):
        for path in root.rglob("*"):
            if path.suffix in {".py", ".sh", ".ps1", ".sql"} and "__pycache__" not in path.parts:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"INSERT\s+INTO\s+users\b", text, re.IGNORECASE):
                    inserts.append(path.relative_to(REPO).as_posix())
    assert inserts == ["backend/app/core/auth/db.py"]
    assert "username" not in UserUpdateRequest.model_fields
    assert "assert_username_writable(username)" in Path(auth_db.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 3. factory


def _two_test_usernames(canvas: dict) -> dict:
    """Due input «Utente che esegue» collegati, con nomi di prova diversi."""
    canvas = copy.deepcopy(canvas)
    first = next(n for n in canvas["nodes"] if n["id"] == "utente")
    first["data"]["preview_username"] = "prova"
    target_id = first["data"]["bindings"][0]["target_node_id"]
    target = next(n for n in canvas["nodes"] if n["id"] == target_id)
    if target["type"] == "api_enrich":
        target["static_parameters"]["altro"] = ""
        field = "static_parameters.altro"
    else:
        twin = copy.deepcopy(target)
        twin["id"] = "f2"
        canvas["nodes"].append(twin)
        canvas["edges"].append({"id": "e-f2", "source": target_id, "target": "f2"})
        target_id, field = "f2", "config.value"
    other = copy.deepcopy(first)
    other["id"], other["data"]["input_key"], other["data"]["preview_username"] = "utente2", "utente2", "altro"
    other["data"]["bindings"] = [{"target_node_id": target_id, "target_field": field}]
    canvas["nodes"].append(other)
    canvas["edges"].append({"id": "b2", "source": "utente2", "target": target_id})
    return canvas


def test_factories_never_raise():
    import inspect

    from app.shared.execution import runtime_identity

    source = inspect.getsource(runtime_identity.RuntimeIdentity)
    assert "raise" not in source
    two = _two_test_usernames(_manipola_canvas())
    assert RuntimeIdentity.preview_of(two).reason == "preview_conflict"


def _arricchisci_node_canvas() -> dict:
    return {"nodes": [
        {"id": "src", "type": "staticValues", "position": POS, "fields": [{"name": "a", "value": "1"}]},
        {"id": "api", "type": "api_enrich", "position": POS, "api_id": "api-1",
         "static_parameters": {"utente": ""}},
        {"id": "out", "type": "result", "position": POS},
        {"id": "utente", "type": "project_input", "position": POS, "data": {
            "input_key": "utente", "label": "Utente", "input_kind": "current_user",
            "bindings": [{"target_node_id": "api", "target_field": "static_parameters.utente"}]}},
    ], "edges": [{"id": "e1", "source": "src", "target": "api"}, {"id": "e2", "source": "api", "target": "out"},
                 {"id": "b", "source": "utente", "target": "api"}]}


def _enrichment_client(monkeypatch, canvas):
    from app.core.arricchisci import router as enrichment

    client, _ = _project_app("arricchisci", canvas, monkeypatch)
    app = client.app
    api = {"id": "api-1", "api_type": "enricher", "parameters": [], "static_parameters": {"utente": ""}}
    monkeypatch.setattr(enrichment.api_connection_service, "get_api_connection", lambda *a, **k: dict(api))
    monkeypatch.setattr(enrichment, "assert_resource_visible", lambda *a, **k: None)
    monkeypatch.setattr(enrichment, "assert_project_visible", lambda *a, **k: None)
    monkeypatch.setattr(enrichment._enrichment_preflight, "required_mapping_error", lambda *a, **k: None)
    app.include_router(enrichment.enrichment_router, prefix="/api")
    return client


def _preview(monkeypatch):
    client, submitted = _project_app("manipola", _two_test_usernames(_manipola_canvas()), monkeypatch)
    response = client.get("/api/projects/p/preview")
    assert not submitted.called
    return response.status_code, _code(response)


def _distinct(monkeypatch):
    canvas = _two_test_usernames(_manipola_canvas())
    client, _ = _project_app("manipola", canvas, monkeypatch)
    response = client.post("/api/projects/p/nodes/out/distinct", json={"canvas": canvas, "column": "owner"})
    return response.status_code, _code(response)


def _validate(monkeypatch):
    from app.core.projects import router as projects

    canvas = _two_test_usernames(_manipola_canvas())
    client, _ = _project_app("manipola", canvas, monkeypatch)
    monkeypatch.setattr(projects, "validate_project_chain", lambda *a, **k: SimpleNamespace(
        to_dict=lambda: {"issues": []}, has_errors=False))
    response = client.post("/api/projects/p/validate", json={"canvas": canvas, "inputs": {}})
    # Valida risponde con il rapporto: il blocco e' una issue con lo stesso codice.
    return 400, response.json()["issues"][0]["code"]


def _node_endpoint(url):
    def call(monkeypatch):
        canvas = _two_test_usernames(_arricchisci_node_canvas())
        client = _enrichment_client(monkeypatch, canvas)
        response = client.post(url, json={"project_id": "p", "api_id": "api-1", "param_mapping": {},
                                          "node_id": "api", "canvas": canvas})
        return response.status_code, _code(response)
    return call


PREVIEW_ENDPOINTS = {
    "anteprima": _preview,
    "valori_distinti": _distinct,
    "valida_con_input": _validate,
    "stima_nodo_api_validate": _node_endpoint("/api/enrichment/validate"),
    "stima_nodo_api_estimate": _node_endpoint("/api/enrichment/estimate"),
}


@pytest.mark.parametrize("name", PREVIEW_ENDPOINTS)
def test_two_test_usernames_give_the_typed_block(name, monkeypatch):
    assert PREVIEW_ENDPOINTS[name](monkeypatch) == (400, REQUIRED)


def test_every_preview_identity_is_enumerated():
    """Ogni ``RuntimeIdentity.preview_of`` dei router ha una riga qui."""
    import inspect

    import app.core.arricchisci.router as arricchisci_router
    import app.core.orchestrator.router as orchestrator_router
    import app.core.projects.router as projects_router

    import app.core.orchestrator.runtime_inputs as runtime_inputs

    count = sum(inspect.getsource(m).count("RuntimeIdentity.preview_of(") for m in (
        arricchisci_router, orchestrator_router, projects_router))
    # anteprima (runtime_inputs.apply_preview_inputs, con i progetti a monte),
    # valori distinti, Valida, stima del nodo API (validate + estimate).
    count += inspect.getsource(runtime_inputs.apply_preview_inputs).count("RuntimeIdentity.preview_of(")
    assert count == 4 and len(PREVIEW_ENDPOINTS) == 5


# ---------------------------------------------------------------- 4. solo quell'input


def _with_other_input(*, user: str | None, broken: bool = False) -> dict:
    """Manipola: un input testo «soglia» collegato al filtro ``g``; con
    ``broken`` collegato a un parametro non collegabile. ``user``: None (nessun
    nodo utente), "unlinked" o "linked" (collegato al filtro ``f``)."""
    canvas = _manipola_canvas(preview_username="prova")
    canvas["nodes"] = [n for n in canvas["nodes"] if n["id"] != "utente"]
    canvas["edges"] = [e for e in canvas["edges"] if e["id"] != "b"]
    canvas["nodes"].append({"id": "g", "type": "transform", "position": POS, "transform_op": "filter",
                            "config": {"column": "n", "operator": "==", "value": "5"}})
    canvas["edges"] += [{"id": "e3", "source": "f", "target": "g"}]
    canvas["edges"] = [e for e in canvas["edges"] if e["id"] != "e2"] + [{"id": "e4", "source": "g", "target": "out"}]
    field = "config.new_column" if broken else "config.value"
    canvas["nodes"].append({"id": "soglia", "type": "project_input", "position": POS, "data": {
        "input_key": "soglia", "label": "Soglia", "input_kind": "text", "required": False,
        "bindings": [{"target_node_id": "g", "target_field": field}]}})
    canvas["edges"].append({"id": "bs", "source": "soglia", "target": "g"})
    if user is not None:
        bindings = [{"target_node_id": "f", "target_field": "config.value"}] if user == "linked" else []
        canvas["nodes"].append({"id": "utente", "type": "project_input", "position": POS, "data": {
            "input_key": "utente", "label": "Utente", "input_kind": "current_user",
            "preview_username": "prova", "bindings": bindings}})
        if user == "linked":
            canvas["edges"].append({"id": "b", "source": "utente", "target": "f"})
    return canvas


def _apply(canvas: dict) -> dict:
    contract = PipelineExecutionContract(project_id="p", project_type="manipola", group_id="g", canvas=canvas,
                                         runtime_context={"default_connection_id": "conn"})
    return apply_identity_inputs(contract, RuntimeIdentity.preview_of(canvas)).canvas


def _others(canvas: dict) -> tuple:
    """Tutto cio' che non e' l'input utente ne' il campo che collega."""
    nodes = []
    for node in canvas["nodes"]:
        if node["id"] == "utente":
            continue
        node = copy.deepcopy(node)
        if node["id"] == "f":
            node["config"].pop("value", None)
        nodes.append(node)
    edges = [e for e in canvas["edges"] if "utente" not in (e["source"], e["target"])]
    return nodes, edges


@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.parametrize("user", ["unlinked", "linked"])
def test_preview_treats_the_other_inputs_as_without_the_user_node(user, broken):
    without = _apply(_with_other_input(user=None, broken=broken))
    with_user = _apply(_with_other_input(user=user, broken=broken))
    assert _others(with_user) == _others(without)
    soglia = next(n for n in with_user["nodes"] if n["id"] == "soglia")
    assert soglia == _with_other_input(user=None, broken=broken)["nodes"][-1]
    if user == "linked":
        assert next(n for n in with_user["nodes"] if n["id"] == "f")["config"]["value"] == "prova"
    assert all(n["id"] != "utente" for n in with_user["nodes"])


def _seen_by(path: str, canvas: dict, monkeypatch) -> dict:
    from app.core.projects import router as projects

    client, submitted = _project_app("manipola", canvas, monkeypatch)
    seen: dict = {}
    if path == "anteprima":
        response = client.get("/api/projects/p/preview")
        assert response.status_code == 200, response.text
        return submitted.call_args.kwargs["execution_contract"]["canvas"]
    if path == "valori_distinti":
        class _Preparation:
            def __init__(self, prepared, group_id):
                seen["canvas"] = prepared
                self.nodes = {n.id: n for n in prepared.nodes}

            def input_ids(self, node_id):
                return []

            def input(self, node_id):
                return pd.DataFrame({"owner": ["a"]}), False, None

        monkeypatch.setattr(projects, "CanvasDataPreparation", _Preparation)
        response = client.post("/api/projects/p/nodes/out/distinct", json={"canvas": canvas, "column": "owner"})
        assert response.status_code == 200, response.text
        return seen["canvas"].model_dump()

    def chain(project_id, load, **_):
        seen["canvas"] = load(project_id)[0]
        return SimpleNamespace(to_dict=lambda: {"issues": []}, has_errors=False)

    monkeypatch.setattr(projects, "validate_project_chain", chain)
    response = client.post("/api/projects/p/validate", json={"canvas": canvas, "inputs": {"soglia": "9"}})
    seen["issues"] = response.json()["issues"]
    return seen


def _comparable(canvas: dict) -> tuple:
    nodes, edges = _others(canvas)
    return ([{k: v for k, v in n.items() if k in ("id", "type", "config", "data") and v} for n in nodes],
            sorted(e["id"] for e in edges))


@pytest.mark.parametrize("path", ["anteprima", "valori_distinti"])
@pytest.mark.parametrize("user", ["unlinked", "linked"])
def test_every_preview_path_treats_the_other_inputs_the_same(path, user, monkeypatch):
    without = _seen_by(path, _with_other_input(user=None), monkeypatch)
    with_user = _seen_by(path, _with_other_input(user=user), monkeypatch)
    assert _comparable(with_user) == _comparable(without)


def test_validate_with_inputs_treats_the_user_input_like_any_input(monkeypatch):
    """Valida con input compila TUTTO il manifest (come «Esegui»): collegato,
    gli altri input si applicano come senza il nodo utente; non collegato,
    e' un errore di configurazione di QUELL'input, identico a quello di un
    input testo non collegato."""
    without = _seen_by("valida_con_input", _with_other_input(user=None), monkeypatch)
    linked = _seen_by("valida_con_input", _with_other_input(user="linked"), monkeypatch)
    assert without["issues"] == linked["issues"] == []
    assert _comparable(linked["canvas"].model_dump()) == _comparable(without["canvas"].model_dump())
    assert next(n for n in linked["canvas"].model_dump()["nodes"] if n["id"] == "g")["config"]["value"] == "9"

    unlinked = _seen_by("valida_con_input", _with_other_input(user="unlinked"), monkeypatch)
    as_text = _with_other_input(user="unlinked")
    next(n for n in as_text["nodes"] if n["id"] == "utente")["data"]["input_kind"] = "text"
    text = _seen_by("valida_con_input", as_text, monkeypatch)
    assert unlinked["issues"] == text["issues"]
    assert [i["code"] for i in unlinked["issues"]] == ["runtime_inputs.invalid"]
    assert "Utente" in unlinked["issues"][0]["message"]
