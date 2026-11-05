"""Input «Utente che esegue» (``current_user``): enumerazione dei percorsi.

Regola (``app/shared/execution/runtime_identity``): il valore e' lo username
di login dell'utente autenticato che avvia, iniettato SOLO dal server.

1. Tabella dei tipi: una sola (``shared/execution/runtime_input_kinds``),
   contratto JSON condiviso con il frontend aggiornato, modelli derivati.
2. Regola unica ``resolve_identity_inputs``: utente -> username; valore del
   client -> rifiutato; senza utente -> blocco tipizzato con il motivo.
3. Enumerazione: ogni percorso da cui passa un input runtime, per ogni caso
   (utente presente / valore dal client / senza utente), ha l'esito atteso.
   Una riga per percorso: un percorso nuovo senza riga fa fallire
   ``test_every_path_is_enumerated``.
4. Impronta: due utenti diversi = due impronte.
5. End-to-end: Estrai con filtro collegato -> WHERE parametrizzato con lo
   username; Manipola con Filtra collegato -> solo le righe dell'utente.
"""

from __future__ import annotations

import copy
import inspect
import json
from types import SimpleNamespace
from typing import get_args
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import require_not_esecutore
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import preflight as eseguibili_preflight
from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.models import ExecutableInputDefinition, ExecutableInputKind
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.projects.canvas_models import CanvasState, Node
from app.shared import error_codes
from app.shared.execution.runtime_identity import (
    RuntimeIdentity,
    RuntimeIdentityError,
    assert_identity_resolved,
    resolve_identity_inputs,
)
from app.shared.execution.runtime_bound_fields import runtime_static_parameters
from app.shared.execution.runtime_input_kinds import (
    RUNTIME_INPUT_KIND_NAMES,
    SERVER_INJECTED_KINDS,
    contract_rows,
)
from tests2.contracts.generate_runtime_input_kinds import KINDS_PATH
from tests2.eseguibili.unit.conftest import build_client
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

MARIO = build_current_user(username="mario.rossi")
ANNA = build_current_user(user_id="u-2", username="anna.bianchi")
REQUIRED = error_codes.RUNTIME_IDENTITY_REQUIRED
CLIENT = error_codes.RUNTIME_IDENTITY_CLIENT_VALUE
POS = {"x": 0, "y": 0}


# ---------------------------------------------------------------- canvas


def _identity(target: str, field: str, **extra) -> dict:
    return {"id": "utente", "type": "project_input", "position": POS, "data": {
        "input_key": "utente", "label": "Utente", "input_kind": "current_user",
        "bindings": [{"target_node_id": target, "target_field": field}], **extra}}


def _estrai_canvas(**identity_extra) -> dict:
    table = {"id": "t", "type": "source", "position": POS, "table_name": "pratiche",
             "columns": [{"name": "id", "type": "int", "visible": True},
                         {"name": "owner", "type": "str", "visible": True}],
             "filters": [{"column": "owner", "operator": "=", "value": ""}]}
    result = {"id": "out", "type": "result", "position": POS, "fileName": "o", "format": "csv"}
    return {"nodes": [table, result, _identity("t", "filters.0.value", **identity_extra)],
            "edges": [{"id": "o1", "source": "t", "target": "out", "join_type": "output"},
                      {"id": "b", "source": "utente", "target": "t"}]}


def _manipola_canvas(**identity_extra) -> dict:
    return {"nodes": [
        {"id": "src", "type": "staticValues", "position": POS, "fields": []},
        {"id": "f", "type": "transform", "position": POS, "transform_op": "filter",
         "config": {"column": "owner", "operator": "==", "value": ""}},
        {"id": "out", "type": "result", "position": POS},
        _identity("f", "config.value", **identity_extra),
    ], "edges": [{"id": "e1", "source": "src", "target": "f"}, {"id": "e2", "source": "f", "target": "out"},
                 {"id": "b", "source": "utente", "target": "f"}]}


def _arricchisci_canvas(**identity_extra) -> dict:
    return {"nodes": [
        {"id": "src", "type": "staticValues", "position": POS, "fields": []},
        {"id": "api", "type": "api_enrich", "position": POS, "api_id": "api-1",
         "static_parameters": {"utente": ""}},
        {"id": "out", "type": "result", "position": POS},
        _identity("api", "static_parameters.utente", **identity_extra),
    ], "edges": [{"id": "e1", "source": "src", "target": "api"}, {"id": "e2", "source": "api", "target": "out"},
                 {"id": "b", "source": "utente", "target": "api"}]}


CANVASES = {"estrai": _estrai_canvas, "manipola": _manipola_canvas, "arricchisci": _arricchisci_canvas}


def _manifest(project_type: str, canvas: dict):
    return compile_manifest(source_project_id="p", project_type=project_type, canvas=canvas,
                            default_connection_id="conn")


def _bound_value(canvas: dict, project_type: str):
    nodes = {n["id"]: n for n in canvas["nodes"]}
    if project_type == "estrai":
        return nodes["t"]["filters"][0]["value"]
    if project_type == "manipola":
        return nodes["f"]["config"]["value"]
    # Nodo API: il bersaglio static_parameters.<nome> si scrive nel campo che
    # l'esecuzione legge (shared.execution.runtime_bound_fields).
    return runtime_static_parameters(nodes["api"])["utente"]


# ---------------------------------------------------------------- 1. tabella


def test_contract_file_is_current():
    assert json.loads(KINDS_PATH.read_text(encoding="utf-8")) == contract_rows(), \
        "rigenerare con python -m tests2.contracts.generate_runtime_input_kinds"


def test_models_use_the_single_table():
    assert get_args(ExecutableInputKind) == RUNTIME_INPUT_KIND_NAMES
    node_kind = Node.model_fields["input_kind"].annotation
    assert set(RUNTIME_INPUT_KIND_NAMES) <= {a for arg in get_args(node_kind) for a in get_args(arg) or (arg,)}
    assert SERVER_INJECTED_KINDS == {"current_user"}


@pytest.mark.parametrize("project_type", CANVASES)
def test_current_user_binds_like_the_other_scalars(project_type):
    """Stesse capability di binding degli scalari: Estrai (filtro), Manipola
    (parametro del passo), Arricchisci (parametro API)."""
    for kind in ("text", "current_user"):
        canvas = CANVASES[project_type]()
        canvas["nodes"][-1]["data"]["input_kind"] = kind
        manifest = _manifest(project_type, canvas)
        assert [(b.target_node_id, b.target_field) for b in manifest.bindings] == \
            [(canvas["nodes"][-1]["data"]["bindings"][0]["target_node_id"],
              canvas["nodes"][-1]["data"]["bindings"][0]["target_field"])]


def test_manifest_ignores_leftovers_and_never_holds_the_test_username():
    canvas = _estrai_canvas(default_value="hacker", required=False, validation={"min_length": 3},
                            preview_username="prova")
    definition = _manifest("estrai", canvas).inputs[0]
    assert (definition.kind, definition.default_value, definition.required) == ("current_user", None, True)
    assert "prova" not in json.dumps(_manifest("estrai", canvas).model_dump())


def test_definition_rejects_a_default_for_server_kinds():
    with pytest.raises(ValueError, match="impostato dal server"):
        ExecutableInputDefinition(key="utente", label="Utente", kind="current_user", default_value="x")


# ---------------------------------------------------------------- 2. regola unica


DEFS = [SimpleNamespace(key="utente", label="Utente", kind="current_user"),
        SimpleNamespace(key="anno", label="Anno", kind="integer")]


def test_rule_authenticated_user_gives_the_username():
    assert resolve_identity_inputs(DEFS, identity=RuntimeIdentity.authenticated(MARIO),
                                   client_values={"anno": 2024}) == {"utente": "mario.rossi"}


@pytest.mark.parametrize("identity", [RuntimeIdentity.authenticated(MARIO), RuntimeIdentity.project_run(),
                                      RuntimeIdentity.unattended()])
def test_rule_client_value_is_rejected_whoever_runs(identity):
    with pytest.raises(RuntimeIdentityError) as err:
        resolve_identity_inputs(DEFS, identity=identity, client_values={"utente": "anna.bianchi"})
    assert err.value.code == CLIENT


@pytest.mark.parametrize("identity,words", [
    (RuntimeIdentity.project_run(), "Eseguibile"),
    (RuntimeIdentity.unattended(), "Pianificazione"),
    (RuntimeIdentity.preview_of(_estrai_canvas()), "nome utente di prova"),
    (RuntimeIdentity.authenticated(build_current_user(username="  ")), "Pianificazione"),
])
def test_rule_without_user_blocks_with_a_clear_message(identity, words):
    with pytest.raises(RuntimeIdentityError) as err:
        resolve_identity_inputs(DEFS, identity=identity, client_values={})
    assert err.value.code == REQUIRED
    assert "Utente" in err.value.message and words in err.value.message


def test_preview_identity_is_the_test_username_only():
    assert RuntimeIdentity.preview_of(_estrai_canvas(preview_username=" prova ")).username == "prova"
    two = _estrai_canvas(preview_username="a")
    other = copy.deepcopy(two["nodes"][-1])
    other["id"], other["data"]["preview_username"] = "utente2", "b"
    two["nodes"].append(other)
    # La factory non solleva (regola del punto 3): il blocco nasce nella
    # risoluzione, dentro il mappatore degli errori di preparazione.
    conflict = RuntimeIdentity.preview_of(two)
    assert (conflict.username, conflict.reason) == (None, "preview_conflict")
    with pytest.raises(RuntimeIdentityError) as err:
        resolve_identity_inputs(DEFS, identity=conflict, client_values={})
    assert err.value.code == REQUIRED and "diversi" in err.value.message


def test_canvases_without_server_inputs_are_untouched():
    assert resolve_identity_inputs(DEFS[1:], identity=RuntimeIdentity.unattended(), client_values={}) == {}
    assert_identity_resolved({"nodes": [{"id": "x", "type": "project_input", "data": {"input_kind": "text"}}]})


# ---------------------------------------------------------------- 3. percorsi


def _code(response) -> str:
    body = response.json()
    detail = body.get("detail", body)
    return detail.get("error") if isinstance(detail, dict) else str(detail)


def _executable_rows(project_type="estrai", canvas=None):
    canvas = canvas or CANVASES[project_type]()
    manifest = _manifest(project_type, canvas).model_dump()
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1", eseguibile_id="exe-1", version_number=1, schema_version=2, project_type=project_type,
        source_project_id="p", source_hash="h", canvas=canvas, manifest=manifest, status="published",
        created_by="u-1", created_at="2026-11-05T00:00:00+00:00")
    executable = eseguibili_db.EseguibileRow(
        id="exe-1", name="Pratiche", description="", canvas=canvas, expected_columns=[],
        default_output_format="csv", group_id="grp-1", created_by="u-1",
        created_at="2026-11-05T00:00:00+00:00", updated_at="2026-11-05T00:00:00+00:00",
        project_type=project_type, active_version_id="version-1", manifest=manifest)
    return executable, version


def _eseguibile_patches(project_type="estrai"):
    executable, version = _executable_rows(project_type)
    return [
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value=executable),
        patch("app.core.eseguibili.router._check_visible", return_value=None),
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router._save_task_owner"),
        patch("app.core.eseguibili.router._task_service.reserve_task_id", return_value="task-1"),
        patch("app.core.eseguibili.service.eseguibili_db.create_eseguibile_run", return_value="run-1"),
        patch("app.core.eseguibili.service.eseguibili_db.bind_eseguibile_run_task"),
        patch("app.core.eseguibili.preflight.api_connection_service.get_api_connection",
              return_value={"id": "api-1", "api_type": "enrichment"}),
    ]


class _Patched:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        return [p.start() for p in self.patches]

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


def _eseguibile_run(case):
    """POST /eseguibili/{id}/runs: l'utente autenticato della richiesta."""
    task = SimpleNamespace(task_id="task-1")
    with _Patched(_eseguibile_patches()), \
         patch("app.core.orchestrator.task_service.task_service.create_pipeline_task", return_value=task) as submit:
        inputs = {"utente": "anna.bianchi"} if case == "client" else {}
        response = build_client(MARIO).post("/api/eseguibili/exe-1/runs", json={"inputs": inputs})
    if response.status_code != 200:
        return "blocked", _code(response)
    contract = submit.call_args.kwargs["execution_contract"]
    assert all(n.get("type") != "project_input" for n in contract["canvas"]["nodes"])
    return "ok", _bound_value(contract["canvas"], "estrai")


def _eseguibile_preflight(case):
    """POST /eseguibili/{id}/preflight: stessa identita' dell'avvio."""
    with _Patched(_eseguibile_patches()):
        inputs = {"utente": "anna.bianchi"} if case == "client" else {}
        response = build_client(MARIO).post("/api/eseguibili/exe-1/preflight", json={"inputs": inputs})
        if response.status_code != 200:
            return "blocked", _code(response)
        executable, _ = _executable_rows()
        by_user = {user.username: eseguibili_preflight.fingerprint_for(
            eseguibile=executable, raw_inputs={}, identity=RuntimeIdentity.authenticated(user)) for user in (MARIO, ANNA)}
    # L'impronta della stima e' quella dell'utente autenticato della richiesta.
    matches = [name for name, fp in by_user.items() if fp == response.json()["preparation_fingerprint"]]
    return "ok", matches[0] if len(matches) == 1 else None


def _project_app(project_type, canvas, monkeypatch, **metadata_extra):
    from app.core.arricchisci import router as enrichment
    from app.core.orchestrator import router as pipeline
    from app.core.projects import router as projects
    from app.core.orchestrator.task_service import task_service

    metadata = SimpleNamespace(project_type=project_type, group_id="grp-1", canvas_validated=True,
                               default_connection_id="conn", **metadata_extra)
    service = SimpleNamespace(
        get_project_metadata=lambda _: metadata,
        get_project_canvas=lambda _: CanvasState(**copy.deepcopy(canvas)),
        get_project_output_contract=lambda _: None,
        get_canvas_version=lambda _: "v1",
    )
    submitted = MagicMock(return_value=SimpleNamespace(task_id="task-1", status=SimpleNamespace(value="pending")))
    for module in (enrichment, projects):
        monkeypatch.setattr(module, "_project_service", service)
        monkeypatch.setattr(module, "_prepare_canvas_for_owner", lambda project_id, c, *a, **k: c)
    monkeypatch.setattr(enrichment, "_api_connection_service", object())
    monkeypatch.setattr(enrichment, "_check_project_ownership", lambda *_: metadata)
    monkeypatch.setattr(enrichment, "assert_group_access", lambda *a, **k: None)
    monkeypatch.setattr(projects, "assert_group_access", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_orchestrator", SimpleNamespace(project_service=service))
    monkeypatch.setattr(pipeline, "_assert_project_group", lambda *_: None)
    monkeypatch.setattr(pipeline, "_task_info_to_dict", lambda _: {"task_id": "task-1"})
    for name in ("create_pipeline_task", "create_enrichment_task", "create_preview_task"):
        monkeypatch.setattr(task_service, name, submitted)
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: MARIO
    app.include_router(enrichment.enrich_router, prefix="/api")
    app.include_router(pipeline.pipeline_router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), submitted


def _project_run(project_type):
    def run(case, monkeypatch):
        client, submitted = _project_app(project_type, CANVASES[project_type](), monkeypatch)
        url = "/api/projects/p/enrich-async" if project_type == "arricchisci" else "/api/projects/p/execute"
        inputs = {"utente": "anna.bianchi"} if case == "client" else {}
        response = client.post(url, json={"inputs": inputs})
        if response.status_code != 200:
            assert not submitted.called
            return "blocked", _code(response)
        return "ok", None
    return run


def _project_preflight(case, monkeypatch):
    client, _ = _project_app("arricchisci", _arricchisci_canvas(), monkeypatch)
    inputs = {"utente": "anna.bianchi"} if case == "client" else {}
    response = client.post("/api/projects/p/preflight", json={"inputs": inputs, "canvas": _arricchisci_canvas()})
    return ("blocked", _code(response)) if response.status_code != 200 else ("ok", None)


def _preview_canvas(case, project_type="manipola"):
    extra = {"preview_username": "prova"} if case in ("preview", "client") else {}
    return CANVASES[project_type](**extra)


def _project_validate(case, monkeypatch):
    """POST /projects/{id}/validate con input = anteprima (username di prova)."""
    from app.core.projects import router as projects
    canvas = _preview_canvas(case)
    client, _ = _project_app("manipola", canvas, monkeypatch)
    seen = {}

    def chain(project_id, load, **_):
        seen["canvas"] = load(project_id)[0]
        return SimpleNamespace(to_dict=lambda: {"issues": []}, has_errors=False)

    monkeypatch.setattr(projects, "validate_project_chain", chain)
    inputs = {"utente": "anna.bianchi"} if case == "client" else {}
    response = client.post("/api/projects/p/validate", json={"canvas": canvas, "inputs": inputs})
    issues = response.json()["issues"]
    if issues:
        return "blocked", issues[0]["code"]
    return "ok", _bound_value(seen["canvas"].model_dump(), "manipola")


def _orchestrator_preview(case, monkeypatch):
    client, submitted = _project_app("manipola", _preview_canvas(case), monkeypatch)
    response = client.get("/api/projects/p/preview")
    if response.status_code != 200:
        assert not submitted.called
        return "blocked", _code(response)
    return "ok", _bound_value(submitted.call_args.kwargs["execution_contract"]["canvas"], "manipola")


def _distinct(case, monkeypatch):
    from app.core.projects import router as projects
    canvas = _preview_canvas(case)
    client, _ = _project_app("manipola", canvas, monkeypatch)
    seen = {}

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
    if response.status_code != 200:
        return "blocked", _code(response)
    return "ok", _bound_value(seen["canvas"].model_dump(), "manipola")


def _node_estimate(case, monkeypatch):
    """Stima del nodo API nell'editor (anteprima): identita' = username di prova."""
    from app.core.arricchisci import router as enrichment
    canvas = _preview_canvas(case, "arricchisci")
    seen = {}

    def preflight(project_id, request, user, *, identity):
        seen["identity"] = identity
        return {"nodes": [{"node_id": "api"}]}

    monkeypatch.setattr(enrichment, "_preflight_enrichment", preflight)
    request = SimpleNamespace(project_id="p", canvas=canvas, node_id="api", api_id=None, param_mapping={}, inputs={})
    enrichment._compatibility_node_preflight(request, MARIO)
    identity = seen["identity"]
    if identity.username is None:
        return "blocked", REQUIRED
    return "ok", identity.username


class _StubExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, *a, **k):
        self.calls += 1
        return {"success": True}

    def preview(self, *a, **k):
        self.calls += 1
        return {"success": True}


def _orchestrator(executor):
    from app.core.orchestrator.service import OrchestratorService
    return OrchestratorService(project_service=MagicMock(), executor=MagicMock(), generator=MagicMock(),
                               connection_service=MagicMock(), estrai_executor=executor,
                               manipola_executor=executor, enrich_executor=executor)


def _unattended_execution(case, monkeypatch):
    """Schedule, catene, Carica, worker pipeline: il contratto arriva agli
    executor con il nodo «Utente che esegue» non risolto."""
    from app.core.orchestrator.execution_contract import build_pipeline_execution_contract
    canvas = _manipola_canvas()
    service = SimpleNamespace(
        get_project_metadata=lambda _: SimpleNamespace(project_type="manipola", group_id="grp-1"),
        get_project_canvas=lambda _: CanvasState(**copy.deepcopy(canvas)),
        get_project_output_contract=lambda _: None)
    contract = build_pipeline_execution_contract(project_id="p", project_service=service)
    executor = _StubExecutor()
    orchestrator = _orchestrator(executor)
    monkeypatch.setattr(orchestrator, "_check_pipeline_memory", lambda: None)
    run = orchestrator.execute_pipeline_from_contract(contract=contract)
    preview = orchestrator.execute_preview_from_contract(contract)
    assert executor.calls == 0
    assert run["error"] == preview["error"]
    return "blocked", run["error"]


# percorso -> {caso: esito atteso}. "user" = utente autenticato (o username
# di prova nelle anteprime), "client" = valore inviato dal browser, "none" =
# nessun utente.
OK_USER = ("ok", "mario.rossi")
OK_PREVIEW = ("ok", "prova")
BLOCKED = ("blocked", REQUIRED)
REJECTED = ("blocked", CLIENT)

PATHS = {
    "eseguibile_run": (_eseguibile_run, {"user": OK_USER, "client": REJECTED}),
    "eseguibile_preflight": (_eseguibile_preflight, {"user": OK_USER, "client": REJECTED}),
    "progetto_esegui_estrai": (_project_run("estrai"), {"none": BLOCKED, "client": REJECTED}),
    "progetto_esegui_manipola": (_project_run("manipola"), {"none": BLOCKED, "client": REJECTED}),
    "progetto_esegui_arricchisci": (_project_run("arricchisci"), {"none": BLOCKED, "client": REJECTED}),
    "progetto_stima_arricchisci": (_project_preflight, {"none": BLOCKED, "client": REJECTED}),
    "valida_con_input": (_project_validate, {"preview": OK_PREVIEW, "none": BLOCKED, "client": REJECTED}),
    "anteprima": (_orchestrator_preview, {"preview": OK_PREVIEW, "none": BLOCKED}),
    "valori_distinti": (_distinct, {"preview": OK_PREVIEW, "none": BLOCKED}),
    "stima_nodo_api": (_node_estimate, {"preview": OK_PREVIEW, "none": BLOCKED}),
    "schedule_catene_worker": (_unattended_execution, {"none": BLOCKED}),
}

MATRIX = [(path, case, expected) for path, (_, cases) in PATHS.items() for case, expected in cases.items()]


@pytest.mark.parametrize("path,case,expected", MATRIX, ids=[f"{p}-{c}" for p, c, _ in MATRIX])
def test_every_path_case(path, case, expected, monkeypatch):
    fn = PATHS[path][0]
    params = inspect.signature(fn).parameters
    outcome = fn(case, monkeypatch) if "monkeypatch" in params else fn(case)
    assert outcome == expected


def test_every_path_is_enumerated():
    """Ogni chiamante di resolve_runtime_inputs / apply_* e ogni ingresso
    degli executor ha una riga in PATHS (raccolta del rapporto)."""
    import app.core.arricchisci.router as arricchisci_router
    import app.core.eseguibili.router as eseguibili_router
    import app.core.orchestrator.router as orchestrator_router
    import app.core.orchestrator.service as orchestrator_service
    import app.core.projects.router as projects_router
    import workers.tasks.enrichment_task as enrichment_task

    sources = {m.__name__: inspect.getsource(m) for m in (
        arricchisci_router, eseguibili_router, orchestrator_router, projects_router)}
    identities = sum(src.count("RuntimeIdentity.") for src in sources.values())
    # eseguibili: preflight + avvio; orchestrator: Esegui + anteprima;
    # arricchisci: stima progetto + enrich-async + stima nodo; progetti:
    # Valida + valori distinti.
    assert identities == 9
    assert inspect.getsource(orchestrator_service).count("assert_identity_resolved(") == 3
    assert "assert_identity_resolved(canvas)" in inspect.getsource(enrichment_task)
    assert len(PATHS) == 11


def test_executor_guard_message_is_clear():
    with pytest.raises(RuntimeIdentityError) as err:
        assert_identity_resolved(_estrai_canvas())
    assert err.value.code == REQUIRED and "Pianificazione" in err.value.message


# ---------------------------------------------------------------- 4. impronta


def test_two_users_have_two_fingerprints():
    with _Patched(_eseguibile_patches()):
        executable, _ = _executable_rows()
        mario = eseguibili_preflight.fingerprint_for(eseguibile=executable, raw_inputs={},
                                                     identity=RuntimeIdentity.authenticated(MARIO))
        anna = eseguibili_preflight.fingerprint_for(eseguibile=executable, raw_inputs={},
                                                    identity=RuntimeIdentity.authenticated(ANNA))
        again = eseguibili_preflight.fingerprint_for(eseguibile=executable, raw_inputs={},
                                                     identity=RuntimeIdentity.authenticated(MARIO))
    assert mario != anna and mario == again


def test_run_with_another_users_fingerprint_is_refused():
    """La stima di Anna non avvia la run di Mario (409 preparation_changed)."""
    with _Patched(_eseguibile_patches()):
        executable, _ = _executable_rows()
        anna = eseguibili_preflight.fingerprint_for(eseguibile=executable, raw_inputs={},
                                                    identity=RuntimeIdentity.authenticated(ANNA))
        response = build_client(MARIO).post("/api/eseguibili/exe-1/runs",
                                            json={"inputs": {}, "preparation_fingerprint": anna})
    assert response.status_code == 409 and "preparation_changed" in response.text


# ---------------------------------------------------------------- 5. end-to-end


@pytest.mark.parametrize("username", ["mario.rossi", "x' OR '1'='1"])
def test_estrai_filter_becomes_a_parametrized_where(username):
    from app.core.estrai import query_plan
    from app.core.estrai.query_generator import QueryGenerator

    canvas = _estrai_canvas()
    resolved, _, summary = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest("estrai", canvas), values={},
        group_id="grp-1", identity=RuntimeIdentity.authenticated(build_current_user(username=username)))
    state = CanvasState(**resolved)
    info = query_plan.sql_query(QueryGenerator(), state.nodes, state.edges,
                                query_plan.estrai_result_node(state.nodes),
                                {"db_type": "postgres", "db_schema": "public"})
    assert "WHERE" in info["sql"] and ":p_0" in info["sql"]
    assert list(info["params"].values()) == [username]
    assert username not in info["sql"]
    assert summary["utente"] == {"kind": "current_user", "provided": True, "username": username}


def test_manipola_filter_keeps_only_the_users_rows():
    from app.core.manipola.plugin_executor import get_executor

    canvas = _manipola_canvas()
    resolved, _, _ = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest("manipola", canvas), values={},
        group_id="grp-1", identity=RuntimeIdentity.authenticated(MARIO))
    config = next(n for n in resolved["nodes"] if n["id"] == "f")["config"]
    frame = pd.DataFrame({"owner": ["mario.rossi", "anna.bianchi", "mario.rossi"], "n": [1, 2, 3]})
    out = get_executor().execute_with_params("filter", [frame], config)
    assert list(out["n"]) == [1, 3]
