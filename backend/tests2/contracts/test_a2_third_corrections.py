"""R1-R5: nomi fisici, identita ripetibili e mapping API sospetti."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.arricchisci.api_connector import ApiConnectionService
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.core.output_contract.service import (
    build_output_contract, ensure_dataframe_matches_contract, node_columns_from_contract,
)
from app.core.output_contract.canvas_sync import _build_column_migrations, _REMOVED_COLUMN
from app.core.projects import schema_execution_sources
from tests2.contracts.test_a2_schema_parity import canvas_of, service_for


CASES = {
    "solo_colonna_vuota": [{"path": "x", "column": ""}],
    "colonna_vuota": [{"path": "x", "column": ""}, {"path": "z", "column": "z"}],
    "colonna_nulla": [{"path": "x", "column": None}, {"path": "z", "column": "z"}],
    "colonna_vuota_nodo": [{"path": "x", "column": ""}],
    "esplosione_vuota": [{"path": "items[*].x", "column": ""}],
    "esplosione_nulla": [{"path": "items[*].x", "column": None}],
    "normale": [{"path": "x", "column": "x"}],
    "path": [{"path": "x"}],
    "autoflatten": [],
}
PAYLOAD = {"x": "2020-01-02", "z": "testo", "items": [{"x": 5}, {"x": 6}]}


def scenario(case, empty=False, generator=False):
    mapping = deepcopy(CASES[case])
    node = {"output_mapping": mapping} if case == "colonna_vuota_nodo" else {}
    api = {"output_mapping": mapping, "options": {"rate_limit": 100000},
           "endpoint": {"url": "https://api.example.com"}}
    if generator:
        api["api_type"] = "generator"
        svc = ApiConnectionService()
        payload = [] if empty else PAYLOAD
        svc._call_generator_endpoint = lambda **kw: (svc._extract_rows_from_response(payload, mapping), payload, None)
        frame = svc._execute_api_generator(api)
    else:
        executor = build_enrichment_executor(node, api)
        executor._call_api_with_retry = lambda *args: (executor._extract_output(PAYLOAD), None)
        frame = executor.execute(pd.DataFrame({"id": pd.Series([] if empty else [1, 2], dtype="int64")}))[0]
    nodes = [{"id": "s", "type": "file_source", "columns": [{"name": "id", "type": "int"}]},
             {"id": "e", "type": "api_enrich", "data": {"api_id": "api", "label": "API sospetta", **node}},
             {"id": "r", "type": "result"}]
    edges = [{"id": "se", "source": "s", "target": "e"}, {"id": "er", "source": "e", "target": "r"}]
    if generator:
        nodes, edges = nodes[1:], edges[1:]
    return frame, canvas_of(nodes, edges), api


def publish(monkeypatch, route, frame, canvas, api, previous=None):
    """Publisher reali; vengono simulati soltanto runtime e storage esterni."""
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: None)
    service = service_for(canvas, "arricchisci").project_service
    captured = []
    service.get_project_output_contract = lambda _: previous
    service.update_project_output_contract = lambda _, value: captured.append(value)
    payload = {"success": True, "columns": list(frame.columns), "output_columns": list(frame.columns),
               "result_id": "result", "rows_processed": len(frame), "rows_count": len(frame),
               "column_dtypes": [str(dtype) for dtype in frame.dtypes]}
    if route == "estrai":
        from app.core.estrai.executor import EstraiExecutor
        EstraiExecutor(service, None, None, None)._salva_tipi_colonne_da_canvas("p", canvas, payload)
    elif route == "enrich":
        from app.core.arricchisci import executor as module
        def execute_enrichment(**kwargs):
            if "finalizer" in kwargs:
                return kwargs["finalizer"](frame, "result", "csv", "p")
            return payload
        monkeypatch.setattr(module.api_connection_service, "_save_and_build_dataframe_response", lambda *a: payload)
        monkeypatch.setattr(module.api_connection_service, "execute_enrichment", execute_enrichment)
        monkeypatch.setattr(module, "append_step", lambda *a, **kw: None)
        assert module.EnrichExecutor(service).execute("p", canvas)["success"]
    elif route == "worker":
        from app.core.arricchisci import enrichment_run_mode as module
        monkeypatch.setattr(module.project_service_module, "ProjectService", lambda: service)
        monkeypatch.setattr(module.pipeline_result_module, "save_pipeline_result_outcome",
                            lambda **kw: SimpleNamespace(result_id="result", output_filename="result.csv"))
        plan = service_for(canvas, "arricchisci").infer_all_schemas("p", canvas)["r"]
        module.StandardRunMode().persist_dataframe_result(project_id="p", task_id="t", final_df=frame,
            final_format="csv", output_filename="result", final_filename="result.csv", group_id="g", column_plan=plan)
    else:
        from app.core.manipola import executor as module
        executor = module.ManipolaExecutor(project_service=service)
        monkeypatch.setattr(executor, "_init_security_context", lambda *a, **kw: "g")
        for name in ("progetto", "file", "datasource", "statiche"):
            monkeypatch.setattr(executor, "_carica_sorgenti_" + name, lambda *a, **kw: None)
        monkeypatch.setattr(executor, "_applica_trasformazioni", lambda *a, **kw: frame)
        monkeypatch.setattr(executor, "_applica_group_by", lambda df, *a, **kw: df)
        monkeypatch.setattr(executor, "_salva_risultato", lambda *a, **kw: payload)
        monkeypatch.setattr(executor, "_costruisci_colonne_con_tipi", lambda *a, **kw: [])
        monkeypatch.setattr(module, "append_step", lambda *a, **kw: None)
        assert executor.execute("p", canvas)["success"]
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("route", ["estrai", "manipola", "enrich", "worker"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("generator", [False, True])
def test_enumerazione_e_tre_esecuzioni(monkeypatch, case, empty, route, legacy, generator):
    frame, canvas, api = scenario(case, empty, generator)
    old = {"columns": [{"id": f"legacy-{i}", "source_name": str(name), "physical_name": str(name)}
                       for i, name in enumerate(frame.columns)]} if legacy else None
    ids = None
    for _ in range(3):
        contract = publish(monkeypatch, route, frame, canvas, api, old)
        assert [col["physical_name"] for col in contract["columns"]] == [str(col) for col in frame.columns]
        ensure_dataframe_matches_contract(dataframe_columns=frame.columns,
            node_columns=node_columns_from_contract(contract), source_project_id="p", dataframe=frame)
        current_ids = [col["id"] for col in contract["columns"]]
        assert len(set(current_ids)) == len(current_ids)
        if ids is not None:
            assert ids == current_ids
        if old:
            migrations = _build_column_migrations(node_columns_from_contract(old), node_columns_from_contract(contract))
            assert _REMOVED_COLUMN not in migrations.values()
        old, ids = contract, current_ids


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("generator", [False, True])
def test_worker_enrich_stesso_contratto(monkeypatch, case, empty, generator):
    frame, canvas, api = scenario(case, empty, generator)
    worker = publish(monkeypatch, "worker", frame, canvas, api)
    enrich = publish(monkeypatch, "enrich", frame, canvas, api)
    assert worker["columns"] == enrich["columns"]


@pytest.mark.parametrize("case", list(CASES)[:8])
@pytest.mark.parametrize("empty", [False, True])
def test_generator_destinazioni_fisiche(monkeypatch, case, empty):
    frame, canvas, api = scenario(case, empty, generator=True)
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    schema = service_for(canvas, "arricchisci").infer_node_schema_typed("p", "e")
    if not empty:
        assert [col["name"] for col in schema.columns] == [str(col) for col in frame.columns]
    if any(item.get("column", "present") in (None, "") for item in api["output_mapping"]):
        assert schema.names_status == "indeterminate"


@pytest.mark.parametrize("case", list(CASES)[:6])
@pytest.mark.parametrize("generator", [False, True])
def test_valida_avviso_dedicato(monkeypatch, case, generator):
    from app.core.projects.canvas_validation import validate_canvas
    from app.core.arricchisci.node_validator import ArricchisciNodeValidator
    monkeypatch.setattr("app.core.projects.canvas_validation.validator_for",
                        lambda kind: ArricchisciNodeValidator() if kind == "api_enrich" else None)
    _, canvas, api = scenario(case, generator=generator)
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    issues = validate_canvas(canvas, project_id="p", project_type="arricchisci").issues
    warnings = [issue for issue in issues if issue.code == "ARRICCHISCI_EMPTY_MAPPING_DESTINATION"]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"
    assert "API sospetta" in warnings[0].message
    assert api["output_mapping"][0]["path"] in warnings[0].message


def test_origine_ambigua_non_dipende_dai_previous_ids():
    rows = [{"id": "piano", "name": name, "source_name": "x", "source_node": "api"} for name in ("", "x")]
    first = build_output_contract(project_id="p", result_columns=rows)
    unrelated = {"columns": [{"id": col.id, "physical_name": "altro", "source_name": "altro"} for col in first.columns]}
    second = build_output_contract(project_id="p", result_columns=rows, previous_contract=unrelated)
    assert [c.id for c in first.columns] == [c.id for c in second.columns]


def test_nomi_non_sostituiti_da_alias_o_path():
    names = ["", None, float("nan"), "x", "x"]
    contract = build_output_contract(project_id="p", result_columns=[
        {"name": name, "alias": "alias", "source_name": "path"} for name in names])
    assert [c.physical_name for c in contract.columns] == [str(name) for name in names]
    frame = pd.DataFrame([[1] * len(names)], columns=[str(name) for name in names])
    ensure_dataframe_matches_contract(dataframe_columns=frame.columns, dataframe=frame,
        node_columns=node_columns_from_contract(contract), source_project_id="p")
