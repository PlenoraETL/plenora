"""``runtime_static_parameters`` conta solo se l'ha scritto la risoluzione
dei binding nella stessa esecuzione (correzione finale prima del deploy,
punto 2; regola in ``app.shared.execution.runtime_bound_fields``).

Enumerazione dei punti d'ingresso di un canvas verso l'esecuzione:

- ``POST /tasks`` (canvas del client): campo tolto all'ingresso e dal worker
  senza contratto;
- «Esegui» del progetto SENZA nodi ``project_input`` (canvas salvati prima di
  84ea5842, clonati, ripristinati, importati): il contratto non ha registro,
  il campo sparisce;
- «Esegui» CON ``project_input``: restano solo le chiavi scritte dalla
  risoluzione (una chiave aggiunta a mano sparisce), anche dopo il passaggio
  dal worker (``from_payload``);
- clone e ripristino: passano dal salvataggio (``update_canvas``) che lo toglie;
- Eseguibili (avvio e stima): il contratto porta il registro della risoluzione.
"""

from __future__ import annotations

import copy
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.orchestrator.execution_contract import (
    PipelineExecutionContract,
    build_pipeline_execution_contract,
)
from app.core.orchestrator.runtime_inputs import apply_identity_inputs, apply_project_runtime_inputs
from app.shared.execution.runtime_bound_fields import (
    RUNTIME_BOUND_REGISTRY,
    RUNTIME_STATIC_PARAMETERS,
    retain_registered,
    runtime_bound_context,
)
from app.shared.execution.runtime_identity import RuntimeIdentity
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.security, pytest.mark.contract]

POS = {"x": 0, "y": 0}
FORGED = {"utente": "admin", "extra": "1"}


def _api_node(**extra) -> dict:
    return {"id": "api", "type": "api_enrich", "position": POS, "api_id": "api-1",
            "data": {"static_parameters": {"utente": "copia", "testo": "copia"}, **extra}}


def _forged_canvas(*, with_input: bool) -> dict:
    nodes = [{"id": "src", "type": "staticValues", "position": POS, "fields": []},
             _api_node(**{RUNTIME_STATIC_PARAMETERS: dict(FORGED)}),
             {"id": "out", "type": "result", "position": POS}]
    edges = [{"id": "e1", "source": "src", "target": "api"}, {"id": "e2", "source": "api", "target": "out"}]
    if with_input:
        nodes.append({"id": "in", "type": "project_input", "position": POS, "data": {
            "input_key": "testo", "label": "Testo", "input_kind": "text", "required": True,
            "bindings": [{"target_node_id": "api", "target_field": "static_parameters.testo"}]}})
        edges.append({"id": "b", "source": "in", "target": "api"})
    return {"nodes": nodes, "edges": edges}


def _runtime_field(canvas: dict) -> dict | None:
    node = next(n for n in canvas["nodes"] if n["id"] == "api")
    return node.get(RUNTIME_STATIC_PARAMETERS) or (node.get("data") or {}).get(RUNTIME_STATIC_PARAMETERS)


def _contract(canvas: dict, **context) -> PipelineExecutionContract:
    return PipelineExecutionContract(project_id="p", project_type="arricchisci", group_id="g",
                                     canvas=canvas, runtime_context=context)


# ------------------------------------------------ regola del lettore (contratto)


def test_contract_without_registry_drops_the_field():
    contract = _contract(_forged_canvas(with_input=False))
    assert _runtime_field(contract.canvas) is None


def test_contract_keeps_only_registered_keys():
    canvas = _forged_canvas(with_input=False)
    contract = _contract(canvas, **{RUNTIME_BOUND_REGISTRY: {"api": {RUNTIME_STATIC_PARAMETERS: ["extra"]}}})
    assert _runtime_field(contract.canvas) == {"extra": "1"}
    assert _runtime_field(canvas) == FORGED  # il canvas del chiamante non cambia


@pytest.mark.parametrize("registry", [None, "x", [], {"api": "x"}, {"api": {RUNTIME_STATIC_PARAMETERS: "utente"}},
                                      {"altro": {RUNTIME_STATIC_PARAMETERS: ["utente"]}}])
def test_malformed_or_foreign_registry_keeps_nothing(registry):
    contract = _contract(_forged_canvas(with_input=False), **{RUNTIME_BOUND_REGISTRY: registry})
    assert _runtime_field(contract.canvas) is None


def test_canvas_without_runtime_fields_is_unchanged():
    canvas = {"nodes": [_api_node()], "edges": []}
    assert retain_registered(canvas, None) is canvas
    assert _contract(copy.deepcopy(canvas)).canvas == canvas


# ------------------------------------------------ «Esegui» del progetto


def _service(canvas: dict):
    metadata = SimpleNamespace(project_type="arricchisci", group_id="g", default_connection_id=None, retention_n=None)
    return SimpleNamespace(get_project_metadata=lambda _: metadata,
                           get_project_canvas=lambda _: copy.deepcopy(canvas),
                           get_project_output_contract=lambda _: None)


def test_project_run_without_project_input_drops_the_field():
    """Canvas salvato prima di 84ea5842, clonato o ripristinato senza input."""
    contract = build_pipeline_execution_contract(project_id="p", project_service=_service(
        _forged_canvas(with_input=False)))
    contract = apply_project_runtime_inputs(contract, {}, identity=RuntimeIdentity.project_run())
    assert _runtime_field(contract.canvas) is None
    worker = PipelineExecutionContract.from_payload(contract.to_payload(), expected_project_id="p")
    assert _runtime_field(worker.canvas) is None


def test_project_run_with_project_input_keeps_only_the_resolved_keys():
    contract = build_pipeline_execution_contract(project_id="p", project_service=_service(
        _forged_canvas(with_input=True)))
    contract = apply_project_runtime_inputs(contract, {"testo": "valore"}, identity=RuntimeIdentity.project_run())
    assert _runtime_field(contract.canvas) == {"testo": "valore"}
    assert contract.runtime_context[RUNTIME_BOUND_REGISTRY] == {"api": {RUNTIME_STATIC_PARAMETERS: ["testo"]}}
    worker = PipelineExecutionContract.from_payload(contract.to_payload(), expected_project_id="p")
    assert _runtime_field(worker.canvas) == {"testo": "valore"}


def test_preview_with_identity_input_keeps_the_resolved_key():
    canvas = _forged_canvas(with_input=False)
    canvas["nodes"].append({"id": "u", "type": "project_input", "position": POS, "data": {
        "input_key": "u", "label": "U", "input_kind": "current_user", "preview_username": "prova",
        "bindings": [{"target_node_id": "api", "target_field": "static_parameters.utente"}]}})
    contract = apply_identity_inputs(_contract(canvas), RuntimeIdentity.preview_of(canvas))
    assert _runtime_field(contract.canvas) == {"utente": "prova"}
    worker = PipelineExecutionContract.from_payload(contract.to_payload(), expected_project_id="p")
    assert _runtime_field(worker.canvas) == {"utente": "prova"}


# ------------------------------------------------ POST /tasks e worker senza contratto


def test_post_tasks_strips_the_field_from_the_client_canvas():
    from app.core.auth.auth import require_not_esecutore
    from app.core.orchestrator.router import tasks_router
    from app.core.orchestrator.task_service import TaskInfo, TaskStatus, TaskType

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: build_current_user()
    app.include_router(tasks_router, prefix="/api")
    info = TaskInfo(task_id="t", task_type=TaskType.ENRICHMENT, project_id="p", status=TaskStatus.PENDING,
                    progress=0, message="queued")
    with patch("app.core.auth.resource_access.assert_project_access"), \
         patch("app.core.auth.resource_access.assert_canvas_resources_access"), \
         patch("app.core.orchestrator.task_service.task_service.submit_task", return_value=info) as submit:
        response = TestClient(app).post("/api/tasks", json={
            "task_type": "enrichment", "project_id": "p", "canvas": _forged_canvas(with_input=False)})
    assert response.status_code == 200, response.text
    assert _runtime_field(submit.call_args.args[0].payload.canvas) is None


def test_worker_without_contract_ignores_the_field(monkeypatch):
    import app.core.arricchisci.enrichment_retry_http as retry_mod
    import workers.tasks.enrichment_task as et_mod
    from app.core.arricchisci.enrichment_service import build_enrichment_executor
    from tests2._helpers.enrichment_task_harness import (
        FakeApiConnectionService,
        install_enrichment_task_mocks,
        invoke_enrichment_task,
    )

    api = {"id": "api-1", "name": "api", "endpoint": {"url": "https://api.example.com/q", "method": "GET"},
           "static_parameters": {"utente": "dalla-connessione"}, "parameters": [], "output_mapping": []}
    install_enrichment_task_mocks(monkeypatch, final_df=pd.DataFrame({"a": ["1"]}))
    monkeypatch.setattr(et_mod, "build_enrichment_executor", build_enrichment_executor)
    monkeypatch.setattr(FakeApiConnectionService, "list_api_connections", lambda self: [{"id": "api-1"}])
    monkeypatch.setattr(FakeApiConnectionService, "get_api_connection", lambda self, *a, **k: copy.deepcopy(api))
    specs: list = []
    monkeypatch.setattr(retry_mod, "validate_external_url", lambda url: None)
    monkeypatch.setattr(retry_mod, "resolve_tls_verify", lambda enabled, *, context: enabled)

    class _Response:
        status_code = 200
        text = ""
        headers: dict = {}
        elapsed_ms = 1.0

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(retry_mod, "execute_http_request", lambda spec: specs.append(spec) or _Response())
    node = _api_node(**{RUNTIME_STATIC_PARAMETERS: dict(FORGED)})
    canvas = {"nodes": [{"id": "src1", "type": "project_source", "data": {"project_id": "test-proj"}}, node,
                        {"id": "res1", "type": "result", "data": {"format": "csv", "fileName": "output"}}],
              "edges": [{"source": "src1", "target": "api"}, {"source": "api", "target": "res1"}]}
    invoke_enrichment_task(project_id="test-proj", canvas=canvas)
    assert specs, "il worker non ha inviato la richiesta"
    assert specs[0].params["utente"] == "dalla-connessione" and "extra" not in specs[0].params


# ------------------------------------------------ clone, ripristino, Eseguibili


def test_clone_and_restore_pass_through_the_save_that_strips():
    from app.core.projects import canvas_service, lifecycle_service

    assert "update_canvas(" in inspect.getsource(lifecycle_service.ProjectLifecycleService.clone_project)
    assert "update_canvas(" in inspect.getsource(canvas_service.ProjectCanvasService.restore_canvas_version)
    assert "strip_runtime_bound_fields(canvas)" in inspect.getsource(
        canvas_service.ProjectCanvasService.update_canvas)


def test_every_resolution_site_hands_its_registry_to_the_contract():
    """Ogni chiamante di ``resolve_runtime_inputs`` che costruisce un contratto
    gli passa il registro (altrimenti i valori risolti sparirebbero)."""
    from app.core.eseguibili import preflight, service
    from app.core.orchestrator import runtime_inputs

    from pathlib import Path

    assert inspect.getsource(service.execute_versioned).count("**runtime_bound_context(canvas)") == 2
    assert "**runtime_bound_context(canvas)" in inspect.getsource(preflight)
    assert inspect.getsource(runtime_inputs).count("runtime_bound_context(resolved_canvas)") == 2
    # Un nuovo chiamante della risoluzione va aggiunto qui (e al registro).
    root = Path(service.__file__).resolve().parents[2]
    callers = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*.py")
        if "resolve_runtime_inputs(" in path.read_text(encoding="utf-8")
        and "def resolve_runtime_inputs(" not in path.read_text(encoding="utf-8")
    )
    assert callers == ["core/eseguibili/preflight.py", "core/eseguibili/service.py",
                       "core/orchestrator/runtime_inputs.py"]


def test_runtime_bound_context_is_empty_without_resolved_fields():
    assert runtime_bound_context({"nodes": [_api_node()], "edges": []}) == {}
