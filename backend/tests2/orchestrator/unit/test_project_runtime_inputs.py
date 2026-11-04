"""Contratti HTTP degli input runtime condivisi dai tre editor."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import require_not_esecutore
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


@pytest.fixture(params=["estrai", "manipola", "arricchisci"])
def editor(request, monkeypatch):
    from app.core.arricchisci import router as enrichment
    from app.core.orchestrator import router as pipeline
    from app.core.orchestrator.task_service import task_service

    kind = request.param
    canvas = {
        "nodes": [
            {"id": "input", "type": "project_input", "data": {
                "input_key": "limit", "input_kind": "integer", "required": True,
                "bindings": [{"target_node_id": "target", "target_field": "config.value"}],
            }},
            {"id": "target", "type": "transform", "data": {"transform_op": "filter", "config": {"value": 1}}},
        ],
        "edges": [{"id": "binding", "source": "input", "target": "target"}],
    }
    original = deepcopy(canvas)
    metadata = SimpleNamespace(project_type=kind, group_id="grp-1", canvas_validated=True, default_connection_id="conn-1")
    service = SimpleNamespace(
        get_project_metadata=lambda _: metadata,
        get_project_canvas=lambda _: SimpleNamespace(nodes=[], model_dump=lambda: deepcopy(canvas)),
        get_project_output_contract=lambda _: None,
    )
    task = SimpleNamespace(task_id="task-1", status=SimpleNamespace(value="pending"))
    submit = Mock(spec=[], return_value=task)
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = build_current_user
    if kind == "arricchisci":
        monkeypatch.setattr(enrichment, "_project_service", service)
        monkeypatch.setattr(enrichment, "_api_connection_service", object())
        monkeypatch.setattr(enrichment, "_check_project_ownership", lambda *_: metadata)
        monkeypatch.setattr(task_service, "create_enrichment_task", submit)
        app.include_router(enrichment.enrich_router, prefix="/api")
        endpoint = "enrich-async"
    else:
        monkeypatch.setattr(pipeline, "_orchestrator", SimpleNamespace(project_service=service))
        monkeypatch.setattr(pipeline, "_assert_project_group", lambda *_: None)
        monkeypatch.setattr(pipeline, "_task_info_to_dict", lambda _: {"task_id": "task-1"})
        monkeypatch.setattr(task_service, "create_pipeline_task", submit)
        app.include_router(pipeline.pipeline_router, prefix="/api")
        endpoint = "execute"
    with TestClient(app) as client:
        yield client, f"/api/projects/project-1/{endpoint}", submit, canvas, original


@pytest.mark.parametrize("value", [42, "42"])
def test_inputs_applied_before_dispatch_without_mutating_saved_canvas(editor, value):
    client, url, submit, canvas, original = editor
    response = client.post(url, json={"inputs": {"limit": value}})
    assert response.status_code == 200, response.text
    contract = submit.call_args.kwargs["execution_contract"]
    assert contract["canvas"]["nodes"] == [
        {"id": "target", "type": "transform", "data": {"transform_op": "filter", "config": {"value": 42}}}
    ]
    assert contract["canvas"]["edges"] == []
    assert contract["runtime_context"]["runtime_input_summary"]["limit"]["provided"] is True
    assert canvas == original


@pytest.mark.parametrize("body,message", [
    ({}, "Input obbligatorio mancante"),
    ({"inputs": {"limit": "invalid"}}, "Valore non valido"),
    ({"inputs": {"limit": True}}, "Valore non valido"),
    ({"inputs": {"unknown": 1}}, "Input non dichiarati"),
])
def test_invalid_inputs_fail_before_dispatch(editor, body, message):
    client, url, submit, _, _ = editor
    response = client.post(url, json=body)
    assert response.status_code == 400, response.text
    assert message in response.json()["detail"]
    submit.assert_not_called()


@pytest.mark.parametrize("provided", [False, True])
def test_file_di_progetto_con_input_text(editor, provided):
    client, url, submit, canvas, _ = editor
    canvas["nodes"][0]["data"]["input_kind"] = "text"
    source = {"id": "file", "type": "project_input", "data": {
        "input_key": "documento", "file_source_id": "file-progetto",
    }}
    canvas["nodes"].append(source)
    original = deepcopy(canvas)
    inputs = {"limit": "testo"}
    if provided:
        inputs["documento"] = {"file_id": "upload"}
    response = client.post(url, json={"inputs": inputs})
    if provided:
        assert response.status_code == 400
        assert "Gli input file runtime sono disponibili solo negli eseguibili" in response.json()["detail"]
        submit.assert_not_called()
    else:
        assert response.status_code == 200, response.text
        resolved = submit.call_args.kwargs["execution_contract"]["canvas"]
        assert resolved["nodes"][-1] == source
        assert resolved["nodes"][0]["data"]["config"]["value"] == "testo"
    assert canvas == original


@pytest.mark.parametrize("provided", [False, True])
def test_connessione_solo_estrai(editor, monkeypatch, provided):
    client, url, submit, canvas, _ = editor
    from app.core.orchestrator import router as pipeline
    kind = "arricchisci" if url.endswith("enrich-async") else pipeline._orchestrator.project_service.get_project_metadata("p").project_type
    canvas["nodes"].append({"id": "conn", "type": "project_input", "data": {
        "input_key": "connessione", "input_kind": "connection",
        "connection_set_id": "set-1", "default_value": "scelta",
    }})
    resolver = Mock(return_value="conn-scelta")
    monkeypatch.setattr("app.core.eseguibili.service.connection_sets.resolve_connection", resolver)
    inputs = {"limit": 42}
    if provided:
        inputs["connessione"] = "scelta"
    response = client.post(url, json={"inputs": inputs})
    if kind == "estrai":
        assert response.status_code == 200, response.text
        contract = submit.call_args.kwargs["execution_contract"]
        assert contract["runtime_context"]["default_connection_id"] == "conn-scelta"
        resolver.assert_called_once()
    else:
        assert response.status_code == 400
        assert "solo nei progetti Estrai" in response.json()["detail"]
        resolver.assert_not_called()
        submit.assert_not_called()


@pytest.mark.parametrize("editor", ["arricchisci"], indirect=True)
def test_arricchisci_senza_input_preserva_contratto(editor):
    client, url, submit, canvas, _ = editor
    from app.core.orchestrator.execution_contract import PipelineExecutionContract
    canvas["nodes"] = [{"id": "file", "type": "file_source", "file_source_id": "file-progetto"}]
    canvas["edges"] = []
    response = client.post(url, json={})
    assert response.status_code == 200, response.text
    payload = submit.call_args.kwargs
    contract = PipelineExecutionContract.from_payload(payload["execution_contract"])
    assert payload["canvas"] == contract.canvas == canvas
    assert contract.group_id == "grp-1"
    assert contract.build_runtime_context()["persist_contract"] is True


@pytest.mark.parametrize("editor", ["arricchisci"], indirect=True)
@pytest.mark.parametrize("group_id", [None, "", "   "])
def test_arricchisci_gruppo_mancante_errore_chiaro(editor, group_id):
    client, url, submit, _, _ = editor
    from app.core.arricchisci import router as enrichment
    enrichment._project_service.get_project_metadata("p").group_id = group_id
    response = client.post(url, json={"inputs": {"limit": 42}})
    assert response.status_code == 400
    assert "Il progetto non ha un gruppo proprietario" in response.json()["detail"]
    submit.assert_not_called()


SCALAR_CASES = [
    ("text", "testo"), ("integer", 7), ("number", 1.5), ("boolean", True),
    ("date", "2026-11-03"), ("datetime", "2026-11-03T12:00:00"), ("select", "opzione"),
]


@pytest.mark.parametrize("kind,value", SCALAR_CASES)
def test_tutti_i_tipi_scalari_per_modalita(editor, kind, value):
    client, url, submit, canvas, _ = editor
    data = canvas["nodes"][0]["data"]
    data["input_kind"] = kind
    if kind == "select":
        data["validation"] = {"options": ["opzione"]}
    response = client.post(url, json={"inputs": {"limit": value}})
    assert response.status_code == 200, response.text
    resolved = submit.call_args.kwargs["execution_contract"]["canvas"]
    assert resolved["nodes"][0]["data"]["config"]["value"] == value


def test_enumerazione_tipi_input_completa():
    from typing import get_args
    from app.core.eseguibili.models import ExecutableInputKind
    assert {kind for kind, _ in SCALAR_CASES} | {"file", "connection"} == set(get_args(ExecutableInputKind))



def test_estrai_consuma_connessione_risolta_nel_contesto(monkeypatch):
    from app.core.estrai.executor import EstraiExecutor
    from app.core.orchestrator.execution_contract import PipelineExecutionContract
    from app.core.orchestrator.runtime_inputs import apply_project_runtime_inputs
    from app.core.projects.canvas_models import CanvasState

    canvas = {"nodes": [
        {"id": "conn", "type": "project_input", "position": {"x": 0, "y": 0}, "data": {
            "input_kind": "connection", "input_key": "cliente", "connection_set_id": "set",
        }},
        {"id": "out", "type": "result", "position": {"x": 0, "y": 0}},
    ], "edges": []}
    monkeypatch.setattr("app.core.eseguibili.service.connection_sets.resolve_connection",
                        lambda *a, **kw: "conn-scelta")
    contract = apply_project_runtime_inputs(PipelineExecutionContract(
        project_id="p", project_type="estrai", group_id="g", canvas=canvas,
        runtime_context={"default_connection_id": "conn-progetto"},
    ), {"cliente": "scelta"})
    service = Mock()
    executor = EstraiExecutor(service, Mock(), Mock(), Mock())
    connection = SimpleNamespace(type="postgres", db_schema="public")
    resolve = Mock(return_value=(connection, "db"))
    monkeypatch.setattr(executor, "_resolve_connection", resolve)
    monkeypatch.setattr(executor, "_assert_connection_visible_for_project", Mock())
    execute = Mock(return_value={"success": True})
    monkeypatch.setattr(executor, "_execute_database", execute)
    monkeypatch.setattr("app.core.estrai.executor.append_step", lambda *a, **kw: None)
    result = executor.execute("p", CanvasState.model_validate(contract.canvas),
                              execution_context=contract.build_runtime_context())
    assert result["success"]
    resolve.assert_called_once_with("conn-scelta")
    assert execute.call_args.args[3] is connection
    assert execute.call_args.kwargs["update_contract"] is True
    service.get_project_metadata.assert_not_called()
