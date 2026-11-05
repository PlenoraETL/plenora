"""Ogni bersaglio collegabile e' LETTO dall'esecuzione (correzione dopo la
conferma dell'input «Utente che esegue», punto 1).

REGOLA (``eseguibili.binding_capabilities.NODE_TARGET_FAMILIES`` +
``shared.execution.runtime_bound_fields``): per ogni tipo di nodo, ogni
bersaglio offerto, collegato a un input, cambia davvero l'input
dell'esecuzione di quel nodo. Il test di enumerazione percorre la tabella:
un tipo di nodo nuovo senza lettore qui fa fallire
``test_every_node_type_has_a_reader``.

Per il nodo API il bersaglio ``static_parameters.<nome>`` si scrive in
``runtime_static_parameters`` (le copie di validazione salvate nei canvas,
Fase 5 T6, non cambiano l'esecuzione) e arriva ai parametri della richiesta
HTTP: esecuzione per riga, generatore, nodo dell'esecuzione del canvas
(EnrichExecutor) e worker.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import app.core.arricchisci.enrichment_retry_http as retry_mod
from app.core.arricchisci import canvas_orchestrator
from app.core.arricchisci.canvas_orchestrator import _merge_node_data
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.core.arricchisci.url_path_params import encode_runtime_path_param, substitute_row_path_params
from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili.binding_capabilities import (
    NODE_TARGET_FAMILIES,
    _transform_registry,
    bindable_targets,
)
from app.core.eseguibili.manifest import compile_manifest
from app.core.projects.canvas_models import CanvasState
from app.shared.execution.runtime_bound_fields import (
    RUNTIME_STATIC_PARAMETERS,
    strip_runtime_bound_fields,
)
from app.shared.execution.runtime_identity import RuntimeIdentity
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

POS = {"x": 0, "y": 0}
MARIO = build_current_user(username="mario.rossi")


# ---------------------------------------------------------------- canvas


def _input(target: str, field: str, *, kind: str = "text") -> dict:
    return {"id": "in", "type": "project_input", "position": POS, "data": {
        "input_key": "x", "label": "X", "input_kind": kind, "required": False,
        "bindings": [{"target_node_id": target, "target_field": field}]}}


def _resolve(canvas: dict, project_type: str, value: Any, *, identity=None) -> dict:
    manifest = compile_manifest(source_project_id="p", project_type=project_type, canvas=canvas,
                                default_connection_id="conn")
    values = {} if identity is not None else {"x": value}
    resolved, _, _ = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="e", canvas=canvas, manifest=manifest, values=values, group_id="g",
        identity=identity or RuntimeIdentity.unattended())
    return resolved


# ---------------------------------------------------------------- lettori


def _transform_reader(resolved: dict, node_id: str, monkeypatch) -> Any:
    """Input dell'esecuzione di un passo di Manipola: i kwargs che
    ``PluginExecutor.execute_node`` passa alla funzione del passo."""
    from app.core.manipola.plugin_executor import get_executor

    executor = get_executor()
    node = next(n for n in CanvasState(**resolved).nodes if n.id == node_id)
    captured: dict = {}
    monkeypatch.setitem(executor.functions, node.transform_op, lambda *a, **kw: captured.update(kw))
    monkeypatch.setattr(executor, "prepare", lambda _tid, _inputs, kwargs, normalized=False: kwargs)
    executor.execute_node(node.transform_op, [pd.DataFrame({"a": [1]})], node)
    return captured


def _sql_reader(resolved: dict, node_id: str, monkeypatch) -> Any:
    """Input dell'esecuzione di Estrai: SQL e parametri della query."""
    from app.core.estrai import query_plan
    from app.core.estrai.query_generator import QueryGenerator

    state = CanvasState(**resolved)
    info = query_plan.sql_query(QueryGenerator(), state.nodes, state.edges,
                                query_plan.estrai_result_node(state.nodes),
                                {"db_type": "postgres", "db_schema": "public"})
    return {"sql": info["sql"], "params": info["params"]}


API_CONFIG = {"id": "api-1", "name": "api", "endpoint": {"url": "https://api.example.com/q", "method": "GET"},
              "static_parameters": {"utente": "dalla-connessione", "fisso": "1"}, "parameters": [],
              "output_mapping": []}


def _api_reader(resolved: dict, node_id: str, monkeypatch) -> Any:
    """Input dell'esecuzione del nodo API: i parametri della richiesta."""
    node = next(n for n in resolved["nodes"] if n["id"] == node_id)
    executor = build_enrichment_executor(_merge_node_data(node), API_CONFIG)
    return executor._build_params(pd.Series({"a": "1"}))


# tipo di nodo -> (progetto, canvas con il nodo "n", lettore)
def _transform_canvases():
    for op, definition in _transform_registry().items():
        config: dict = {}
        for parameter in definition.get("parameters") or []:
            if parameter.get("type") == "condition_list":
                config[parameter["id"]] = [{"column": "a", "operator": "==", "value": "", "result": ""}]
            depends = parameter.get("depends_on") or {}
            if depends.get("param"):
                allowed = depends.get("value")
                config.setdefault(depends["param"], (allowed[0] if isinstance(allowed, list) else
                                                     "a" if allowed == "_any_" else allowed))
        node = {"id": "n", "type": "transform", "position": POS, "transform_op": op, "config": config}
        yield op, node


def _estrai_canvas(node: dict) -> dict:
    nodes = [node] if node["type"] == "result" else [node, {"id": "out", "type": "result", "position": POS}]
    if node["type"] == "result":
        nodes.insert(0, {"id": "t", "type": "source", "position": POS, "table_name": "pratiche",
                         "columns": [{"name": "owner", "type": "str", "visible": True}]})
    edges = [{"id": "e", "source": nodes[0]["id"], "target": nodes[1]["id"], "join_type": "output"}]
    return {"nodes": nodes, "edges": edges}


SOURCE = {"id": "n", "type": "source", "position": POS, "table_name": "pratiche",
          "columns": [{"name": "owner", "type": "str", "visible": True}],
          "filters": [{"column": "owner", "operator": "=", "value": ""},
                      {"column": "owner", "operator": "IN", "value": ""}]}
RESULT = {"id": "n", "type": "result", "position": POS, "fileName": "o", "format": "csv",
          "group_by_enabled": True, "group_by_columns": ["owner"],
          "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}],
          "having_enabled": True,
          "having_conditions": [{"function": "COUNT", "column": "*", "operator": ">", "value": "0"}]}
API = {"id": "n", "type": "api_enrich", "position": POS, "api_id": "api-1",
       # Copia di validazione (T6): non e' quella che l'esecuzione legge.
       "static_parameters": {"utente": "copia-salvata", "fisso": "1"}}


def _cases():
    for op, node in _transform_canvases():
        canvas = {"nodes": [node, {"id": "out", "type": "result", "position": POS}],
                  "edges": [{"id": "e", "source": "n", "target": "out"}]}
        yield f"transform:{op}", "manipola", canvas, _transform_reader
    yield "source", "estrai", _estrai_canvas(copy.deepcopy(SOURCE)), _sql_reader
    yield "result", "estrai", _estrai_canvas(copy.deepcopy(RESULT)), _sql_reader
    yield "api_enrich", "arricchisci", {
        "nodes": [{"id": "src", "type": "staticValues", "position": POS, "fields": []}, copy.deepcopy(API),
                  {"id": "out", "type": "result", "position": POS}],
        "edges": [{"id": "e1", "source": "src", "target": "n"}, {"id": "e2", "source": "n", "target": "out"}],
    }, _api_reader


READERS = {"transform": _transform_reader, "source": _sql_reader, "result": _sql_reader,
           "api_enrich": _api_reader}

CASES = [(name, project_type, canvas, reader, target.path, target.value_type)
         for name, project_type, canvas, reader in _cases()
         for target in bindable_targets(next(n for n in canvas["nodes"] if n["id"] == "n"))]


def _values_for(value_type: str, target_path: str, canvas: dict) -> tuple[Any, Any]:
    if value_type == "boolean":
        return "true", "false"
    if value_type == "number":
        return "11", "22"
    if value_type == "select":
        node = next(n for n in canvas["nodes"] if n["id"] == "n")
        definition = _transform_registry()[node["transform_op"]]
        parameter = next(p for p in definition["parameters"] if p["id"] == target_path.split(".")[1])
        options = [o.get("value") if isinstance(o, dict) else o for o in parameter.get("options") or []]
        return options[0], options[-1]
    return "VALORE_A", "VALORE_B"


def test_every_node_type_has_a_reader():
    assert set(NODE_TARGET_FAMILIES) == set(READERS)
    offered = {name.split(":")[0] for name, *_ in CASES}
    assert offered == set(READERS), "ogni tipo di nodo della tabella offre almeno un bersaglio nel test"
    assert len(CASES) > 70


def test_types_without_a_reader_offer_nothing():
    """Prima ``filters``/``conditions``/``value``/``default_value`` erano
    offerti su qualunque nodo, anche dove nessun esecutore li legge."""
    for node_type in ("staticValues", "project_input", "file_source", "feature_source", "carica",
                      "multi_join", "datasource_node", "comment"):
        node = {"id": "x", "type": node_type, "filters": [{"column": "a", "operator": "=", "value": ""}],
                "conditions": [{"value": "", "result": ""}], "value": "", "default_value": "",
                "static_parameters": {"k": ""}, "having_enabled": True, "group_by_enabled": True,
                "having_conditions": [{"value": "0"}]}
        assert bindable_targets(node) == [], node_type
    # Operatori senza valore e HAVING spento non leggono il valore.
    assert bindable_targets({"type": "source", "filters": [{"column": "a", "operator": "IS NULL", "value": ""}]}) == []
    assert bindable_targets({**RESULT, "group_by_enabled": False}) == []
    assert bindable_targets({**RESULT, "having_enabled": False}) == []
    # Parametro di un passo con la dipendenza non soddisfatta: l'esecuzione
    # non lo passa alla funzione (prima «Ordine crescente» era offerto ma
    # dipende da order_column == "_any_", mai vero).
    paths = [t.path for t in bindable_targets({"type": "transform", "transform_op": "add_row_number",
                                               "config": {"order_column": "a"}})]
    assert "config.ascending" not in paths


@pytest.mark.parametrize("name,project_type,canvas,reader,path,value_type", CASES,
                         ids=[f"{c[0]}:{c[4]}" for c in CASES])
def test_every_offered_target_changes_the_execution_input(name, project_type, canvas, reader, path,
                                                          value_type, monkeypatch):
    first, second = _values_for(value_type, path, canvas)
    outputs = []
    for value in (first, second):
        bound = copy.deepcopy(canvas)
        bound["nodes"].append(_input("n", path))
        bound["edges"].append({"id": "b", "source": "in", "target": "n"})
        outputs.append(json.dumps(reader(_resolve(bound, project_type, value), "n", monkeypatch),
                                  sort_keys=True, default=str))
    assert outputs[0] != outputs[1], f"{name}: il bersaglio {path} non cambia l'esecuzione"
    if value_type == "string":
        assert "VALORE_A" in outputs[0] and "VALORE_B" in outputs[1]


# ---------------------------------------------------------------- nodo API


def _api_canvas(url_param: str = "utente") -> dict:
    return {"nodes": [
        {"id": "src", "type": "staticValues", "position": POS, "fields": []},
        {"id": "api", "type": "api_enrich", "position": POS, "api_id": "api-1",
         "data": {"static_parameters": {url_param: "copia-salvata"}}},
        {"id": "out", "type": "result", "position": POS},
        {"id": "utente", "type": "project_input", "position": POS, "data": {
            "input_key": "utente", "label": "Utente", "input_kind": "current_user",
            "bindings": [{"target_node_id": "api", "target_field": f"static_parameters.{url_param}"}]}},
    ], "edges": [{"id": "e1", "source": "src", "target": "api"}, {"id": "e2", "source": "api", "target": "out"},
                 {"id": "b", "source": "utente", "target": "api"}]}


def _resolved_api_node(username: str = "mario.rossi", url_param: str = "utente") -> dict:
    canvas = _api_canvas(url_param)
    resolved = _resolve(canvas, "arricchisci", None,
                        identity=RuntimeIdentity.authenticated(build_current_user(username=username)))
    return next(n for n in resolved["nodes"] if n["id"] == "api")


class _Response:
    status_code = 200
    text = ""
    headers: dict = {}
    elapsed_ms = 1.0

    def json(self):
        return {"ok": True}


def _capture_http(monkeypatch) -> list:
    specs: list = []
    monkeypatch.setattr(retry_mod, "validate_external_url", lambda url: None)
    monkeypatch.setattr(retry_mod, "resolve_tls_verify", lambda enabled, *, context: enabled)
    monkeypatch.setattr(retry_mod, "execute_http_request", lambda spec: specs.append(spec) or _Response())
    return specs


def test_binding_writes_the_execution_field_and_keeps_the_saved_copy():
    node = _resolved_api_node()
    assert node["data"][RUNTIME_STATIC_PARAMETERS] == {"utente": "mario.rossi"}
    assert node["data"]["static_parameters"] == {"utente": "copia-salvata"}


def test_saved_copies_never_change_existing_projects():
    """Senza binding: si inviano i parametri della connessione attuale, mai
    la copia salvata nel nodo, e un campo runtime salvato si toglie."""
    node = {"id": "api", "type": "api_enrich", "api_id": "api-1",
            "data": {"static_parameters": {"utente": "copia-salvata"},
                     RUNTIME_STATIC_PARAMETERS: {"utente": "forgiato"}}}
    canvas = {"nodes": [node], "edges": []}
    strip_runtime_bound_fields(canvas)
    assert RUNTIME_STATIC_PARAMETERS not in canvas["nodes"][0]["data"]
    params = build_enrichment_executor(_merge_node_data(canvas["nodes"][0]), API_CONFIG)._build_params(
        pd.Series({"a": "1"}))
    assert params == {"utente": "dalla-connessione", "fisso": "1"}


def test_resolution_ignores_a_runtime_field_saved_in_the_canvas():
    canvas = _api_canvas()
    canvas["nodes"][1]["data"][RUNTIME_STATIC_PARAMETERS] = {"altro": "forgiato"}
    resolved = _resolve(canvas, "arricchisci", None, identity=RuntimeIdentity.authenticated(MARIO))
    api = next(n for n in resolved["nodes"] if n["id"] == "api")
    assert api["data"][RUNTIME_STATIC_PARAMETERS] == {"utente": "mario.rossi"}


def test_canvas_preparation_and_save_strip_the_runtime_field():
    import inspect

    from app.core.projects import canvas_service, router as projects_router

    state = CanvasState(nodes=[{"id": "api", "type": "api_enrich", "position": POS,
                                "data": {RUNTIME_STATIC_PARAMETERS: {"k": "v"}},
                                RUNTIME_STATIC_PARAMETERS: {"k": "v"}}], edges=[])
    strip_runtime_bound_fields(state)
    dumped = state.model_dump()["nodes"][0]
    assert RUNTIME_STATIC_PARAMETERS not in dumped and RUNTIME_STATIC_PARAMETERS not in dumped["data"]
    assert "strip_runtime_bound_fields(canvas)" in inspect.getsource(projects_router._prepare_canvas_for_owner)
    assert "strip_runtime_bound_fields(canvas)" in inspect.getsource(
        canvas_service.ProjectCanvasService.update_canvas)


@pytest.mark.parametrize("username", ["mario.rossi", "x' OR '1'='1"])
def test_per_row_execution_sends_the_username(username, monkeypatch):
    specs = _capture_http(monkeypatch)
    node = _resolved_api_node(username)
    executor = build_enrichment_executor(_merge_node_data(node), API_CONFIG)
    executor.execute(pd.DataFrame({"a": ["1"]}))
    assert specs and all(spec.params["utente"] == username for spec in specs)
    assert specs[0].params["fisso"] == "1"


@pytest.mark.parametrize("username,expected", [
    ("mario/..", "mario%2F.."), ("a%2Fb", "a%252Fb"), ("a?b=1", "a%3Fb%3D1"), ("a#b", "a%23b"),
])
def test_per_row_path_parameter_cannot_change_the_path(username, expected, monkeypatch):
    specs = _capture_http(monkeypatch)
    node = _resolved_api_node(username)
    config = {**API_CONFIG, "endpoint": {"url": "https://api.example.com/users/{utente}/data", "method": "GET"}}
    build_enrichment_executor(_merge_node_data(node), config).execute(pd.DataFrame({"a": ["1"]}))
    assert specs[0].url == f"https://api.example.com/users/{expected}/data"
    assert "utente" not in specs[0].params


def test_canvas_node_execution_sends_the_username(monkeypatch):
    """Nodo dell'esecuzione del canvas (EnrichExecutor -> run_single_enricher):
    enricher per riga e generatore."""
    specs = _capture_http(monkeypatch)
    node = _resolved_api_node()
    canvas_orchestrator.run_single_enricher(SimpleNamespace(), node, {"api-1": API_CONFIG},
                                            pd.DataFrame({"a": ["1"]}))
    assert specs[0].params["utente"] == "mario.rossi"

    seen: dict = {}
    from app.core.arricchisci.api_connection_generator_enricher import ApiConnectionGeneratorEnricherMixin

    svc = SimpleNamespace(_execute_api_generator=lambda config, df: seen.update(
        params=ApiConnectionGeneratorEnricherMixin._build_generator_base_params(None, config, df)))
    canvas_orchestrator.run_single_enricher(svc, node, {"api-1": {**API_CONFIG, "api_type": "generator"}}, None)
    assert seen["params"]["utente"] == "mario.rossi" and seen["params"]["fisso"] == "1"


def test_generator_runtime_value_is_not_a_template():
    from app.core.arricchisci.api_connection_generator_enricher import ApiConnectionGeneratorEnricherMixin

    config = {**API_CONFIG, RUNTIME_STATIC_PARAMETERS: {"utente": "{{NOW}}"}}
    params = ApiConnectionGeneratorEnricherMixin._build_generator_base_params(None, config, None)
    assert params["utente"] == "{{NOW}}"


def test_estimate_uses_the_same_parameters(monkeypatch):
    from app.core.arricchisci import canvas_preflight

    seen: list = []
    original = build_enrichment_executor

    def spy(data, api, **kwargs):
        executor = original(data, api, **kwargs)
        seen.append(executor._build_params(pd.Series({"a": "1"})))
        return executor

    monkeypatch.setattr(canvas_preflight, "build_enrichment_executor", spy)
    monkeypatch.setattr(canvas_preflight, "prepare_api_request", lambda api: None)
    node = _resolved_api_node()
    canvas_preflight.estimate_api_input(_merge_node_data(node), API_CONFIG, pd.DataFrame({"a": ["1"]}))
    assert seen[0]["utente"] == "mario.rossi"


def test_worker_sends_the_username(monkeypatch):
    """Worker di Arricchisci (``execute_enrichment_task``): il nodo API
    risolto arriva alla richiesta HTTP con lo username."""
    import workers.tasks.enrichment_task as et_mod
    from tests2._helpers.enrichment_task_harness import (
        FakeApiConnectionService,
        install_enrichment_task_mocks,
        invoke_enrichment_task,
    )

    install_enrichment_task_mocks(monkeypatch, final_df=pd.DataFrame({"a": ["1"]}))
    monkeypatch.setattr(et_mod, "build_enrichment_executor", build_enrichment_executor)
    monkeypatch.setattr(FakeApiConnectionService, "list_api_connections", lambda self: [{"id": "api-1"}])
    monkeypatch.setattr(FakeApiConnectionService, "get_api_connection",
                        lambda self, *a, **k: copy.deepcopy(API_CONFIG))
    specs = _capture_http(monkeypatch)
    node = _resolved_api_node()
    canvas = {"nodes": [{"id": "src1", "type": "project_source", "data": {"project_id": "test-proj"}}, node,
                        {"id": "res1", "type": "result", "data": {"format": "csv", "fileName": "output"}}],
              "edges": [{"source": "src1", "target": "api"}, {"source": "api", "target": "res1"}]}
    # Il canvas risolto arriva al worker nel contratto, con il registro dei
    # campi scritti dalla risoluzione (regola del lettore).
    from app.core.orchestrator.execution_contract import PipelineExecutionContract
    from app.shared.execution.runtime_bound_fields import runtime_bound_context

    contract = PipelineExecutionContract(project_id="test-proj", project_type="arricchisci", group_id="g",
                                         canvas=canvas, runtime_context=runtime_bound_context(canvas))
    invoke_enrichment_task(project_id="test-proj", canvas=canvas, execution_contract=contract.to_payload())
    assert specs, "il worker non ha inviato la richiesta"
    assert specs[0].params["utente"] == "mario.rossi"


# ---------------------------------------------------------------- percorso URL


@pytest.mark.parametrize("value,encoded", [
    ("/..", "%2F.."), ("%2F", "%252F"), ("?", "%3F"), ("#", "%23"), ("a/b", "a%2Fb"), ("45.4,9.2", "45.4%2C9.2"),
])
def test_path_parameter_is_fully_encoded(value, encoded):
    url, rest = substitute_row_path_params("https://h/api/{p}/x", {"p": value, "q": "1"},
                                           runtime_keys=frozenset({"p"}))
    assert url == f"https://h/api/{encoded}/x" and rest == {"q": "1"}


@pytest.mark.parametrize("value", ["", ".", ".."])
def test_dot_segments_and_empty_values_are_refused(value):
    with pytest.raises(ValueError, match="percorso"):
        encode_runtime_path_param("p", value)


def test_runtime_values_reach_the_strict_rule_on_every_execution_path():
    """Per riga e generatore passano le chiavi runtime a ``url_path_params``
    (la parita' con HEAD dei valori non runtime: test_url_path_params_parity)."""
    import inspect

    from app.core.arricchisci import api_connection_generator_enricher, enrichment_retry_http

    for module in (enrichment_retry_http, api_connection_generator_enricher):
        source = inspect.getsource(module)
        assert "runtime_path_keys(" in source and "RUNTIME_STATIC_PARAMETERS" in source
