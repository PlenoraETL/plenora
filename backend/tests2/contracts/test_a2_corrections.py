"""Regressioni del giro di conferma A2: identità, tipi e boundary HTTP."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import get_current_user
from app.core.projects import router as projects, schema_execution_sources
from app.core.projects.schema_inference import SchemaInferenceService
from app.core.output_contract.service import build_output_contract, result_columns_from_plan, node_columns_from_contract
from app.core.output_contract.canvas_sync import _build_column_migrations, _REMOVED_COLUMN
from app.shared.contracts.auth import CurrentUser
from tests2.contracts.test_a2_schema_parity import sql_case, SQL_CASES, canvas_of, service_for, MAPPING_CASES
from tests2.contracts.inference_runtime_cases import CASES, EXTRA
from tests2.contracts.test_inference_runtime_parity import frames
from tests2.contracts.test_a1_correction import canvas_frames


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "sqlite")


def inferred(canvas, kind="estrai", node="r", **kwargs):
    svc = service_for(canvas, kind)
    if kwargs:
        svc.api_configs = kwargs
    return svc.infer_all_schemas("p", canvas)[node]


@pytest.mark.parametrize("case", ["singola", "alias", "join", "group_alias", "manuale"])
def test_origin_identity_alias_reorder_add_and_legacy_contract(case):
    canvas = sql_case(case)
    before = inferred(canvas)
    changed = deepcopy(canvas)
    if case == "group_alias":
        changed.nodes[-1].aggregations[0].alias = "nuova_media"
    elif case == "manuale":
        changed.nodes[0].calculated_columns[0]["sql"] = "COALESCE(a.valore, 0) AS nuovo"
    else:
        for node in changed.nodes:
            for col in node.columns or []:
                col.alias = f"nuovo_{node.id}_{col.name}"
            if node.columns:
                node.columns.reverse()
    after = inferred(changed)
    origins = lambda cols: {(c.get("source_node"), c["source_name"]): c["id"] for c in cols}
    assert origins(before) == origins(after)
    legacy = build_output_contract(project_id="p", result_columns=[dict(c, id=f"legacy-{i}") for i, c in enumerate(before)])
    extra = {"name": "aggiunta", "source_name": "aggiunta", "source_node": "new", "type": "int"}
    updated = build_output_contract(project_id="p", previous_contract=legacy, result_columns=after + [extra])
    assert {c.id for c in legacy.columns} <= {c.id for c in updated.columns}
    migrations = _build_column_migrations(node_columns_from_contract(legacy), node_columns_from_contract(updated))
    assert all(value is not _REMOVED_COLUMN for value in migrations.values())


@pytest.mark.parametrize("generator", [False, True])
def test_api_path_identity_rename_reorder_add(generator):
    canvas = canvas_of([{"id": "e", "type": "api_enrich", "data": {"api_id": "api"}}], [])
    config = {"api_type": "generator" if generator else "rest", "output_mapping": [{"path": "x", "column": "email"}]}
    first = inferred(canvas, "arricchisci", "e", api=config)
    config["output_mapping"] = [{"path": "z", "column": "nuova"}, {"path": "x", "column": "posta"}]
    second = inferred(canvas, "arricchisci", "e", api=config)
    assert first[0]["id"] == second[1]["id"]


def test_partial_plan_and_declared_types():
    plan = [{"name": "alias", "source_name": "fisica", "id": "stable", "type": "int"},
            {"name": "somma", "id": "sum", "type": "float"}]
    columns = result_columns_from_plan(project_id="p", columns=["fisica", "expr", "somma"], plan=plan, types=[None, "float64", None])
    assert [c["type"] for c in columns] == ["int", "float64", "float"]
    assert [columns[i]["id"] for i in [0, 2]] == ["stable", "sum"]
    assert columns[1]["id"] not in {"stable", "sum"}


def test_manual_without_alias_keeps_certain_columns():
    canvas = sql_case("manuale")
    canvas.nodes[0].calculated_columns[0]["sql"] = "a.valore * 2"
    response = service_for(canvas, "estrai").infer_schemas_response("p", canvas)
    for node in ["a", "r"]:
        assert [c["name"] for c in response["schemas"][node]] == ["id", "valore"]
        assert response["schema_states"][node]["names_status"] == "indeterminate"


def test_provider_unreadable_keeps_origin_and_type(monkeypatch):
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: (_ for _ in ()).throw(ValueError("unavailable")))
    columns = inferred(sql_case("singola"))
    assert [c["type"] for c in columns] == ["int", "int"]
    assert columns[0]["id"] == "id-a"


def test_arcgis_ignores_alias_like_execution():
    canvas = canvas_of([{"id": "a", "type": "feature_source", "columns": [{"name": "n", "alias": "display", "type": "int"}]},
                       {"id": "r", "type": "result"}], [{"id": "ar", "source": "a", "target": "r"}])
    assert [(c["name"], c["type"]) for c in inferred(canvas)] == [("n", "int")]


HTTP_CASES = [("a1-" + op, canvas_frames(op, frames(op, False), dict(config))) for op, config in CASES.items()]
HTTP_CASES += [(f"a1-extra-{i}-{op}", canvas_frames(op, frames(op, False), dict(config))) for i, (op, config) in enumerate(EXTRA)]
HTTP_CASES += [("a2-" + case, sql_case(case)) for case in SQL_CASES]
HTTP_CASES += [(f"api-{i}", canvas_of([{"id": "e", "type": "api_enrich", "data": {"api_id": "api", **data}}], [])) for i, (data, _) in enumerate(MAPPING_CASES)]
HTTP_CASES += [("anonymous", canvas_frames("string_extract", frames("string_extract", False), {"column": "s", "pattern": "(a)(b)"}))]


@pytest.mark.parametrize("name,canvas", HTTP_CASES, ids=[name for name, _ in HTTP_CASES])
def test_get_post_every_canvas(name, canvas, monkeypatch):
    svc = SimpleNamespace(get_project_canvas=lambda _: canvas,
                          get_project_metadata=lambda _: SimpleNamespace(id="p", group_id="g", project_type="estrai" if name.startswith("a2") else "manipola", default_connection_id=None))
    monkeypatch.setattr(projects, "_project_service", svc)
    monkeypatch.setattr(projects, "_schema_service", SchemaInferenceService(svc, api_configs={"api": {}}))
    app = FastAPI()
    app.include_router(projects.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u", username="u", role="superadmin", group_id="g")
    with TestClient(app) as client:
        for response in [client.get("/projects/p/schemas"), client.post("/projects/p/schemas", json={"canvas": canvas.model_dump(mode="json")})]:
            assert response.status_code == 200, response.text
            assert set(response.json()["schemas"]) == {n.id for n in canvas.nodes}


def test_generator_empty_destination_and_enricher_response_use_distinct_phases():
    from app.core.arricchisci.enrichment_service import build_enrichment_executor
    canvas = canvas_of([{"id": "e", "type": "api_enrich", "data": {"api_id": "api"}}], [])
    config = {"output_mapping": [{"column": "", "path": "x"}]}
    assert inferred(canvas, "arricchisci", "e", api=config)[0]["name"] == ""
    assert build_enrichment_executor({}, config)._extract_output({"x": 4}) == [{"x": 4}]



@pytest.mark.parametrize("case", ["singola", "alias", "join", "calcolata", "manuale"])
def test_add_and_reorder_sql_columns_keep_ids(case):
    canvas = sql_case(case)
    before = inferred(canvas)
    canvas.nodes[0].columns.append(canvas.nodes[0].columns[0].model_copy(update={"id": None, "name": "extra", "alias": None}))
    canvas.nodes[0].columns.reverse()
    after = inferred(canvas)
    by_origin = {(c.get("source_node"), c["source_name"]): c["id"] for c in after}
    assert all(by_origin[c.get("source_node"), c["source_name"]] == c["id"] for c in before)


def test_manipola_rename_and_contract_metadata_changes_are_not_removals():
    canvas = canvas_frames("rename", frames("rename", False), {"renames": [{"old_name": "a", "new_name": "Primo"}]})
    before = inferred(canvas, "manipola", "t")
    canvas.nodes[-1].config = {"renames": [{"old_name": "a", "new_name": "Secondo"}]}
    after = inferred(canvas, "manipola", "t")
    assert [c["id"] for c in before] == [c["id"] for c in after]
    old = build_output_contract(project_id="p", result_columns=before)
    # Tipi corretti e source_name riportato all'origine non cambiano l'identit?.
    after[0].update(type="int")
    new = build_output_contract(project_id="p", previous_contract=old, result_columns=after)
    assert [c.id for c in old.columns] == [c.id for c in new.columns]
    assert all(value is not _REMOVED_COLUMN for value in _build_column_migrations(node_columns_from_contract(old), node_columns_from_contract(new)).values())


def test_legacy_without_source_node_upgrades_without_churn():
    columns = inferred(sql_case("join"))
    legacy = build_output_contract(project_id="p", result_columns=[dict(c, id=f"legacy-{i}") for i, c in enumerate(columns)]).model_dump()
    for col in legacy["columns"]:
        col.pop("source_node")
        col["source_name"] = col["physical_name"]
    new = build_output_contract(project_id="p", previous_contract=legacy, result_columns=columns)
    assert [c.id for c in new.columns] == [c["id"] for c in legacy["columns"]]


def test_schema_failure_is_node_local(monkeypatch):
    canvas = sql_case("singola")
    svc = service_for(canvas, "estrai")
    original = svc._infer_node_schema_unchecked
    def fail_one(node, *args, **kwargs):
        if node.id == "r":
            raise ValueError("errore locale")
        return original(node, *args, **kwargs)
    monkeypatch.setattr(svc, "_infer_node_schema_unchecked", fail_one)
    response = svc.infer_schemas_response("p", canvas)
    assert response["schemas"]["a"]
    assert response["schemas"]["r"] == response["schemas"]["a"]
    assert response["schema_states"]["r"]["names_status"] == "indeterminate"


@pytest.mark.parametrize("scenario", ["manuale", "arcgis", "provider"])
def test_estrai_publisher_preserves_real_or_declared_types(scenario, monkeypatch):
    from app.core.estrai.executor import EstraiExecutor
    if scenario == "arcgis":
        canvas = canvas_of([{"id": "a", "type": "feature_source", "columns": [{"name": "id", "alias": "display", "type": "int"}, {"name": "valore", "type": "float"}]}, {"id": "r", "type": "result"}], [{"id": "e", "source": "a", "target": "r"}])
        result = {"columns": ["id", "valore"]}
    else:
        canvas = sql_case("manuale")
        canvas.nodes[0].calculated_columns[0]["sql"] = "a.valore * 2"
        result = {"columns": ["id", "valore", "a.valore * 2"], "column_types": ["int64", "int64", "float64"]}
    svc = service_for(canvas, "estrai").project_service
    svc.get_project_output_contract = lambda _: None
    svc.update_project_output_contract = lambda *args: None
    if scenario == "provider":
        monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: (_ for _ in ()).throw(ValueError("unavailable")))
    executor = EstraiExecutor(svc, None, None, None)
    executor._salva_tipi_colonne_da_canvas("p", canvas, result)
    columns = result["output_contract"]["columns"]
    assert [col["type"]["logical"] for col in columns] == (["integer", "number"] if scenario == "arcgis" else ["integer", "integer", "number"])
    if scenario != "arcgis":
        assert columns[0]["id"] == "id-a"
