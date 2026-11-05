"""A3, correzione dopo la conferma: punti 3, 5 e 6 (regole, non casi singoli).

- 3: la configurazione HTTP non supportata NON blocca il costruttore
  dell'esecutore (come HEAD: RowError per riga); Valida e preflight la
  segnalano con la stessa funzione ``prepare_api_request``.
- 5: validate/estimate senza ``node_id``/``canvas`` = HEAD (sonda
  ``scratchpad/a3fix/p_compat.py``: 90 risposte identiche); con node_id il
  preflight è limitato al nodo e ai suoi antenati, mai 500.
- 6: un unico mappatore degli errori della preparazione (distinct e
  preflight): ogni famiglia della tabella → 4xx con ``details.reason``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import require_not_esecutore

pytestmark = [pytest.mark.unit, pytest.mark.contract]

USER = SimpleNamespace(user_id="u", group_id="g", role="admin")
BAD_HTTP = [
    ({"endpoint": {"url": "https://e.org", "method": "TRACE"}}, "Metodo HTTP non supportato: TRACE"),
    ({"endpoint": {"url": "https://e.org", "method": "POST"}, "request": {"body_type": "xml"}}, "body_type non supportato: xml"),
    ({"endpoint": {"url": "https://e.org", "method": "GET"}, "response": {"format": "csv", "delimiter": ";;"}},
     "response delimiter CSV deve essere un singolo carattere"),
]


# 3 -----------------------------------------------------------------------------

@pytest.mark.parametrize("extra,message", BAD_HTTP)
def test_unsupported_http_config_is_row_error_at_runtime_and_error_in_validation(extra, message):
    from app.core.arricchisci.canvas_preflight import estimate_api_input
    from app.core.arricchisci.enrichment_service import build_enrichment_executor
    from app.core.arricchisci.request_preparation import prepare_api_request
    api = {"parameters": [{"name": "q", "required": True}], "output_mapping": [{"path": "v", "column": "v"}],
           "options": {"retry_max": 0, "rate_limit": 0}, **extra}
    executor = build_enrichment_executor({"param_mapping": {"q": "a"}}, api)  # come HEAD: nessun rifiuto qui
    with pytest.raises(ValueError, match=message):
        prepare_api_request(api)
    with pytest.raises(ValueError, match=message):
        estimate_api_input({"param_mapping": {"q": "a"}}, api, pd.DataFrame({"a": [1, 2]}))
    if "delimiter" not in json.dumps(extra):
        frame, result = executor.execute(pd.DataFrame({"a": [1, 2]}))
        assert len(frame) == 2
        assert {(error.error_type, error.error_message) for error in result.errors} == {("request_config_error", message)}


@pytest.mark.parametrize("extra,message", BAD_HTTP)
def test_validate_canvas_reports_unsupported_http_config(monkeypatch, extra, message):
    from app.core.projects import schema_execution_sources
    from app.core.projects.canvas_models import CanvasState
    from app.core.projects.canvas_validation import validate_canvas
    api = {"parameters": [], "output_mapping": [], **extra}
    from app.core.arricchisci.api_connector import api_connection_service
    monkeypatch.setattr(api_connection_service, "get_api_connection", lambda *args, **kwargs: api)
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    canvas = CanvasState.model_validate({"nodes": [
        {"id": "s", "type": "staticValues", "position": {"x": 0, "y": 0}, "fields": [{"name": "a", "value": "1", "field_type": "integer"}]},
        {"id": "e", "type": "api_enrich", "position": {"x": 0, "y": 0}, "api_id": "api"}],
        "edges": [{"id": "e1", "source": "s", "target": "e"}]})
    from app.core.arricchisci.node_validator import register_arricchisci_validator
    register_arricchisci_validator()
    report = validate_canvas(canvas, project_id="p", project_type="arricchisci")
    assert any(issue.node_id == "e" and issue.severity == "error" and issue.message == message for issue in report.issues)


# 5 -----------------------------------------------------------------------------

@pytest.fixture
def enrichment_client(monkeypatch):
    from app.core.arricchisci import router as R
    apis = {"enr": {"id": "enr", "api_type": "enricher", "endpoint": {"url": "https://e.org", "method": "GET"},
                    "parameters": [{"name": "q", "required": True}]}}
    monkeypatch.setattr(R.api_connection_service, "get_api_connection", lambda api_id, include_token=False: dict(apis[api_id]))
    for name in ("assert_resource_visible", "assert_project_visible", "assert_file_source_access", "assert_group_access"):
        monkeypatch.setattr(R, name, lambda *a, **k: None)
    canvas = {"nodes": [
        {"id": "s", "type": "staticValues", "position": {"x": 0, "y": 0}},
        {"id": "n1", "type": "api_enrich", "position": {"x": 0, "y": 0}, "api_id": "enr"},
        {"id": "n2", "type": "api_enrich", "position": {"x": 0, "y": 0}, "api_id": "enr"},
        {"id": "x", "type": "staticValues", "position": {"x": 0, "y": 0}}],
        "edges": [{"id": "e1", "source": "s", "target": "n1"}, {"id": "e2", "source": "x", "target": "n2"}]}
    seen = {}
    monkeypatch.setattr(R, "_project_service", SimpleNamespace(
        get_project_canvas=lambda _: SimpleNamespace(model_dump=lambda: json.loads(json.dumps(canvas))),
        get_project_metadata=lambda _: SimpleNamespace(project_type="arricchisci", group_id="g", updated_at=None),
        get_canvas_version=lambda _: "c1:v"))
    monkeypatch.setattr(R, "_prepare_canvas_for_owner", lambda project_id, canvas, *a, **k: canvas)
    monkeypatch.setattr(R, "prepare_contract", lambda contract, inputs, **_: (contract, SimpleNamespace(model_dump=lambda: {})))
    monkeypatch.setattr(R, "_preflight_connections", lambda canvas, user: apis)

    def estimate(contract, connections):
        ids = [node["id"] for node in contract.canvas["nodes"]]
        seen["ids"] = ids
        if "n2" in ids:  # l'altro ramo è rotto: non deve contare per n1
            raise ValueError("nodo n2 rotto")
        return [{"node_id": "n1", "valid": True, "status": "estimated", "api_calls": 3, "input_rows": 4,
                 "reason": None, "validation": {"valid_rows": 4, "invalid_rows": 0}}]
    monkeypatch.setattr(R, "estimate_canvas", estimate)
    app = FastAPI()
    app.include_router(R.enrichment_router)
    app.dependency_overrides[require_not_esecutore] = lambda: USER
    return TestClient(app, raise_server_exceptions=False), seen


@pytest.mark.parametrize("endpoint", ["validate", "estimate"])
def test_node_path_is_limited_to_node_and_ancestors(enrichment_client, endpoint):
    client, seen = enrichment_client
    response = client.post(f"/enrichment/{endpoint}", json={"api_id": "enr", "project_id": "p", "param_mapping": {"q": "a"}, "node_id": "n1"})
    assert response.status_code == 200, response.json()
    assert sorted(seen["ids"]) == ["n1", "s"]
    assert response.json()["status"] in {"verified_with_data", "estimated"}


@pytest.mark.parametrize("endpoint", ["validate", "estimate"])
@pytest.mark.parametrize("body,reason", [
    ({"node_id": "assente"}, "node_not_unique"),
    ({"canvas": {"nodes": [], "edges": []}}, "node_not_unique"),
    ({"node_id": "n2"}, "invalid_configuration"),
])
def test_node_path_errors_are_typed_never_500(enrichment_client, endpoint, body, reason):
    client, _ = enrichment_client
    response = client.post(f"/enrichment/{endpoint}", json={"api_id": "enr", "project_id": "p", "param_mapping": {"q": "a"}, **body})
    assert response.status_code == 422, response.json()
    assert reason in json.dumps(response.json())


def test_node_path_keeps_head_required_mapping_branch(enrichment_client):
    client, _ = enrichment_client
    response = client.post("/enrichment/validate", json={"api_id": "enr", "project_id": "p", "param_mapping": {}, "node_id": "n1"})
    assert response.status_code == 200 and response.json()["valid"] is False and response.json()["missing_columns"] == ["q"]


# 6 -----------------------------------------------------------------------------

def _family_instances():
    from app.core.manipola.canvas_data_preparation import PREPARATION_ERROR_FAMILIES
    from app.core.dataframes.tabular import DATAFRAME_PARSE_ERRORS
    from app.core.artifacts.types import SourceLoadingError
    samples = [ValueError("v"), KeyError("k"), TypeError("t"), IndexError("i"), AttributeError("a"),
               ZeroDivisionError("z"), OSError("o"), FileNotFoundError("f"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x"),
               RuntimeError("r"), SourceLoadingError("s")] + [family("p") for family in DATAFRAME_PARSE_ERRORS]
    for families, *_ in PREPARATION_ERROR_FAMILIES:
        for family in families:
            if not any(isinstance(sample, family) for sample in samples):
                samples.append(family.__new__(family))
    return samples


@pytest.mark.parametrize("error", _family_instances(), ids=lambda error: type(error).__name__)
def test_every_preparation_error_family_maps_to_4xx_with_reason(monkeypatch, error):
    from app.core.projects import router as P
    from app.core.manipola import canvas_data_preparation as C
    monkeypatch.setattr(P, "_project_service", SimpleNamespace(get_project_metadata=lambda _: SimpleNamespace(group_id="g")))
    monkeypatch.setattr(P, "assert_group_access", lambda *a, **k: None)
    from app.core.arricchisci import router as R
    for module in (P, R):  # anche il call-site che ne tiene una copia (guardia monkeypatch)
        monkeypatch.setattr(module, "_prepare_canvas_for_owner", lambda project_id, canvas, *a, **k: canvas)

    def fail(self, node_id):
        raise error
    monkeypatch.setattr(C.CanvasDataPreparation, "input", fail)
    app = FastAPI()
    app.include_router(P.router)
    app.dependency_overrides[require_not_esecutore] = lambda: USER
    canvas = {"nodes": [{"id": "s", "type": "staticValues", "position": {"x": 0, "y": 0}},
                        {"id": "l", "type": "transform", "position": {"x": 0, "y": 0}, "transform_op": "lookup"}],
              "edges": [{"id": "e", "source": "s", "target": "l"}]}
    response = TestClient(app, raise_server_exceptions=False).post("/projects/p/nodes/l/distinct", json={"canvas": canvas, "column": "a"})
    assert 400 <= response.status_code < 500, response.text
    assert C.classify_preparation_error(error).reason in response.text
