"""Parità A2: SQL eseguito e API simulate, senza rete né database esterni."""
import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.projects.canvas_models import CanvasState
from app.core.projects.schema_inference import SchemaInferenceService
from app.core.projects import schema_execution_sources
from app.core.estrai.query_generator import QueryGenerator
from app.core.dataframes.tabular import make_unique_column_names
from app.core.dataframes.output_columns import apply_output_columns
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.core.output_contract.service import build_output_contract, result_columns_from_plan


def canvas_of(nodes, edges):
    return CanvasState.model_validate({"nodes": [{"position": {"x": 0, "y": 0}, **n} for n in nodes], "edges": edges})


def service_for(canvas, kind):
    return SchemaInferenceService(SimpleNamespace(
        get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(project_type=kind, default_connection_id="db-test" if kind == "estrai" else None),
    ))


@pytest.fixture(autouse=True)
def sqlite_provider(monkeypatch):
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "sqlite")


def sql_case(case):
    source = {"id": "a", "type": "source", "table_name": "prima", "alias": "a",
              "columns": [{"name": "id", "type": "int", "id": "id-a"}, {"name": "valore", "type": "int"}]}
    result = {"id": "r", "type": "result"}
    nodes = [source, result]
    edges = [{"id": "ar", "source": "a", "target": "r"}]
    if case in {"join", "duplicati", "collisione"}:
        other = {"id": "b", "type": "source", "table_name": "seconda", "alias": "b",
                 "columns": [{"name": "id", "type": "int"}, {"name": "valore", "type": "int"}]}
        if case == "join":
            other["columns"][1]["alias"] = "destra"
        if case == "collisione":
            source["columns"][1]["alias"] = "id_2"
        nodes.insert(1, other)
        edges = [{"id": "ab", "source": "a", "target": "b", "join_type": "inner",
                  "source_column": "id", "target_column": "id"}, {"id": "br", "source": "b", "target": "r"}]
    if case == "alias":
        source["columns"][1]["alias"] = "Totale"
    if case == "nascosta":
        source["columns"][0]["visible"] = False
    if case == "group":
        result.update(group_by_enabled=True, group_by_columns=["id"], aggregations=[{"column": "valore", "function": "SUM"}])
    if case == "group_alias":
        result.update(group_by_enabled=True, group_by_columns=["id"], aggregations=[{"column": "valore", "function": "AVG", "alias": "media"}])
    if case == "calcolata":
        source["calculated_columns"] = [{"name": "doppio", "expression": "a.valore * 2", "output_type": "int"}, {"name": "incompleta"}]
    if case == "manuale":
        source["calculated_columns"] = [{"name": "nome_canvas", "is_manual": True, "sql": "COALESCE(a.valore, 0) AS effettivo"}]
    if case == "output_columns":
        result["output_columns"] = [{"name": "id", "keep": False}, {"name": "valore", "label": "rinominata"}]
    return canvas_of(nodes, edges)


SQL_CASES = ["singola", "join", "alias", "nascosta", "group", "group_alias", "calcolata", "manuale", "duplicati", "collisione", "output_columns"]


def execute_sql_case(canvas):
    query = QueryGenerator("sqlite").generate_sql(canvas.nodes, canvas.edges, "r")
    with sqlite3.connect(":memory:") as db:
        db.executescript("CREATE TABLE prima(id INTEGER, valore INTEGER); CREATE TABLE seconda(id INTEGER, valore INTEGER);"
                         "INSERT INTO prima VALUES (1, 2), (1, 3); INSERT INTO seconda VALUES (1, 4);")
        cursor = db.execute(query["sql"], query["params"])
        names, _ = make_unique_column_names(item[0] for item in cursor.description)
    return query["sql"], names


@pytest.mark.parametrize("case", SQL_CASES)
def test_sql_reale_e_inferenza(case):
    canvas = sql_case(case)
    _, names = execute_sql_case(canvas)
    schema = service_for(canvas, "estrai").infer_node_schema_typed("p", "r")
    assert [col["name"] for col in schema.columns] == names
    assert schema.names_status == schema.order_status == "certain"
    assert len({col["id"] for col in schema.columns}) == len(names)
    if case in {"group", "group_alias", "manuale"}:
        assert schema.types_status == "indeterminate"


MAPPING_CASES = [
    ({"output_mapping": [{"path": "x", "column": "vecchio"}]}, [{"path": "x", "column": "corrente"}]),
    ({}, [{"path": "x", "column": "nuova"}]),
    ({"output_mapping": [{"path": "z"}]}, []),
    ({}, [{"path": "x", "column": "nuova"}, {"path": "z", "column": "nuova"}]),
    ({}, [{"path": "x", "column": "id"}]),
    ({"output_mapping": [{"path": "z", "column": "prima"}, {"path": "x", "column": "seconda"}]}, []),
]


def execute_mapping(node_data, api_config, empty=False):
    executor = build_enrichment_executor(node_data, api_config)
    executor._call_api_with_retry = lambda *args: (executor._extract_output({"x": 5, "z": "testo"}), None)
    df = pd.DataFrame({"id": pd.Series([] if empty else [1, 2], dtype="int64")})
    return executor, executor.execute(df)[0]


@pytest.mark.parametrize("node_data,mapping", MAPPING_CASES)
@pytest.mark.parametrize("empty", [False, True])
def test_mapping_effettivo(monkeypatch, node_data, mapping, empty):
    api = {"output_mapping": mapping, "options": {"rate_limit": 100000}}
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: deepcopy(api))
    canvas = canvas_of([
        {"id": "s", "type": "file_source", "columns": [{"name": "id", "type": "int"}]},
        {"id": "e", "type": "api_enrich", "data": {"api_id": "api", **node_data}},
    ], [{"id": "se", "source": "s", "target": "e"}])
    executor, frame = execute_mapping(node_data, api, empty)
    schema = service_for(canvas, "arricchisci").infer_node_schema_typed("p", "e")
    assert [col["name"] for col in schema.columns] == list(frame.columns)
    assert schema.names_status == "certain"
    assert schema.types_status == "indeterminate"
    assert len({col["id"] for col in schema.columns}) == len(frame.columns)


def test_output_primo_arco_senza_groupby():
    canvas = canvas_of([
        {"id": "z", "type": "file_source", "columns": [{"name": "id", "id": "i", "type": "int"}]},
        {"id": "a", "type": "file_source", "columns": [{"name": "altro"}]},
        {"id": "r", "type": "result", "group_by_enabled": True, "group_by_columns": ["assente"],
         "output_columns": [{"name": "id", "label": "pubblica"}]},
    ], [{"id": "zr", "source": "z", "target": "r"}, {"id": "ar", "source": "a", "target": "r"}])
    schema = service_for(canvas, "arricchisci").infer_node_schema_typed("p", "r")
    frame = apply_output_columns(pd.DataFrame({"id": [1]}), canvas.nodes[-1].output_columns)
    assert [col["name"] for col in schema.columns] == list(frame.columns)
    assert schema.columns[0]["id"] == "i"
    from app.core.arricchisci.canvas_orchestrator import run_enrichment_canvas
    from app.shared.execution.canvas_graph import CanvasGraph
    graph = CanvasGraph.from_canvas(canvas)
    svc = SimpleNamespace(_load_file_source=lambda node, **kwargs: pd.DataFrame({"id": [1]}) if node["id"] == "z" else pd.DataFrame({"altro": [2]}))
    actual = run_enrichment_canvas(svc, "p", graph=graph, ctx=object(), api_connections={},
                                  execution_order=["z", "a", "r"], groups=[("single", [key]) for key in ["z", "a", "r"]])
    assert list(actual["r"].columns) == [col["name"] for col in schema.columns]


@pytest.mark.parametrize("api", [{}, {"api_type": "generator"}, {"connection_type": "rest_file"}])
def test_nomi_dinamici(monkeypatch, api):
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: {"id": "api", **api})
    canvas = canvas_of([{"id": "e", "type": "api_enrich", "data": {"api_id": "api"}}], [])
    schema = service_for(canvas, "arricchisci").infer_node_schema_typed("p", "e")
    assert schema.names_status == schema.order_status == "indeterminate"


def test_contract_legacy_physical_prevale_su_id_non_verificabili():
    before = build_output_contract(project_id="p", result_columns=[{"id": "a", "name": "x"}, {"id": "b", "name": "y"}])
    after = build_output_contract(project_id="p", previous_contract=before,
                                  result_columns=[{"id": "a", "name": "y"}, {"id": "b", "name": "x"}])
    assert [(col.id, col.physical_name) for col in after.columns] == [("b", "y"), ("a", "x")]
    bound = result_columns_from_plan(project_id="p", columns=["y", "x"], plan=[{"id": "a", "name": "y"}, {"id": "b", "name": "x"}])
    assert [col["id"] for col in bound] == ["a", "b"]


def test_tutti_i_tipi_editor_hanno_gestore():
    root = Path(__file__).resolve().parents[3]
    service = SchemaInferenceService(None)
    handlers = service._build_node_type_handlers()
    for editor in ["EstraiEditor", "ManipolaEditor", "ArricchisciEditor"]:
        text = (root / "frontend/src/pages" / f"{editor}.jsx").read_text(encoding="utf-8")
        block = re.search(r"const nodeTypes\s*=\s*\{(.*?)\};", text, re.S).group(1)
        block = re.sub(r"//[^\n]*", "", block)
        keys = []
        for entry in block.split(","):
            if not entry.strip():
                continue
            match = re.fullmatch(r"\s*['\"]?(\w+)['\"]?\s*(?::\s*\w+)?\s*", entry)
            assert match, f"Dichiarazione nodeTypes da censire esplicitamente: {entry}"
            keys.append(match.group(1))
        assert keys
        assert not {key for key in keys if service.EDITOR_NODE_ALIASES.get(key, key) not in handlers}


@pytest.mark.parametrize("payload", [[], [{"x": 1}, {"x": 2}], {"data": [{"x": 3}]}, {"x": 4}])
def test_generator_mapping_corrente_ignora_ingresso_e_snapshot(monkeypatch, payload):
    from app.core.arricchisci.api_connector import ApiConnectionService
    api = {"api_type": "generator", "endpoint": {"url": "https://api.example.com"},
           "output_mapping": [{"path": "x", "column": "corrente"}]}
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    svc = ApiConnectionService()
    svc._call_generator_endpoint = lambda **kwargs: (svc._extract_rows_from_response(payload, api["output_mapping"]), payload, None)
    frame = svc._execute_api_generator(api, pd.DataFrame({"ingresso": [1]}))
    canvas = canvas_of([
        {"id": "s", "type": "file_source", "columns": [{"name": "ingresso"}]},
        {"id": "e", "type": "api_enrich", "data": {"api_id": "api", "output_mapping": [{"path": "x", "column": "obsoleta"}]}},
    ], [{"id": "se", "source": "s", "target": "e"}])
    schema = service_for(canvas, "arricchisci").infer_node_schema_typed("p", "e")
    assert [col["name"] for col in schema.columns] == list(frame.columns)
    assert schema.names_status == "certain"
    assert schema.types_status == "indeterminate"


def test_normalizzazione_oracle_e_id_stabili(monkeypatch):
    canvas = sql_case("alias")
    svc = service_for(canvas, "estrai")
    svc.project_service.get_project_metadata = lambda _: SimpleNamespace(project_type="estrai", default_connection_id="oracle")
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "oracle")
    before = svc.infer_node_schema_typed("p", "r")
    assert [col["name"] for col in before.columns] == ["id", "totale"]
    canvas.nodes[0].columns.reverse()
    after = svc.infer_node_schema_typed("p", "r")
    assert {col["name"]: col["id"] for col in before.columns} == {col["name"]: col["id"] for col in after.columns}


def test_postgres_alias_quotati_e_non_quotati(monkeypatch):
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "postgres")
    canvas = sql_case("alias")
    canvas.nodes[0].calculated_columns = [{"name": "manuale", "is_manual": True, "sql": 'a.valore AS "ConMaiuscola"'}]
    schema = service_for(canvas, "estrai").infer_node_schema_typed("p", "r")
    assert [col["name"] for col in schema.columns] == ["id", "totale", "ConMaiuscola"]


@pytest.mark.parametrize("sql", ["a.valore * 2", "a.id AS primo, a.valore AS secondo"])
def test_sql_manuale_non_risolto_non_e_certo(sql):
    canvas = sql_case("singola")
    canvas.nodes[0].calculated_columns = [{"name": "nome_canvas", "is_manual": True, "sql": sql}]
    schema = service_for(canvas, "estrai").infer_node_schema_typed("p", "r")
    assert schema.names_status == "indeterminate"


def test_contract_colonna_opzionale_assente_preserva_id():
    plan = [{"name": "a", "id": "a"}, {"name": "b", "id": "b", "optional": True}, {"name": "c", "id": "c"}]
    bound = result_columns_from_plan(project_id="p", columns=["a", "c"], plan=plan)
    assert [col["id"] for col in bound] == ["a", "c"]


def test_http_stati_nomi_certi_tipi_incerti():
    canvas = sql_case("group")
    response = service_for(canvas, "estrai").infer_schemas_response("p")
    assert response["schema_states"]["r"] == {
        "names_status": "certain", "order_status": "certain", "types_status": "indeterminate",
        "reason": "Tipo di espressioni SQL o aggregazioni non verificato dal database",
    }
    assert all({"id", "name", "type", "source_name", "optional"} <= col.keys() for col in response["schemas"]["r"])


def test_tipo_sorgente_non_dichiarato_non_diventa_certo():
    canvas = canvas_of([
        {"id": "s", "type": "source", "table_name": "prima", "columns": [{"name": "id"}]},
        {"id": "r", "type": "result"},
    ], [{"id": "sr", "source": "s", "target": "r"}])
    schema = service_for(canvas, "estrai").infer_node_schema_typed("p", "r")
    assert schema.names_status == schema.order_status == "certain"
    assert schema.types_status == "indeterminate"
