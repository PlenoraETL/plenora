"""E1-E4: invarianti di identita, precedenza tipi e mapping storico."""
import ast
from datetime import date
from itertools import permutations
from pathlib import Path
from typing import get_args

import pandas as pd
import pytest

from app.core.dataframes.schema import resolve_column_type, _LOGICAL_TYPE_COMPATIBLE_DTYPES
from app.core.output_contract.models import OutputColumnType
from app.core.output_contract.service import build_output_contract, result_columns_from_plan, node_columns_from_contract
from app.core.output_contract.canvas_sync import _build_column_migrations, _REMOVED_COLUMN
from tests2.contracts.test_a2_schema_parity import canvas_of, service_for, execute_mapping


@pytest.mark.parametrize("order", list(permutations(range(3))))
@pytest.mark.parametrize("legacy_source", ["physical", "origin"])
def test_abc_existing_physical_never_inherits_another_id(order, legacy_source):
    names = ["id", "id_2", "id_terza"]
    old = {"columns": [{"id": f"old-{i}", "physical_name": name,
            "source_name": name if legacy_source == "physical" else "id"} for i, name in enumerate(names)]}
    new = build_output_contract(project_id="p", previous_contract=old, result_columns=[
        {"id": "old-2", "name": names[i], "source_name": "id", "source_node": "abc"[i]} for i in order])
    assert {c.physical_name: c.id for c in new.columns} == dict(zip(names, ["old-0", "old-1", "old-2"]))
    migrations = _build_column_migrations(node_columns_from_contract(old), node_columns_from_contract(new))
    assert not any(a != b and b in names for a, b in migrations.items() if b is not _REMOVED_COLUMN)


def test_ambiguous_source_gets_new_id_and_removal():
    old = {"columns": [{"id": "old", "source_name": "id", "physical_name": "gone"}]}
    new = build_output_contract(project_id="p", previous_contract=old, result_columns=[
        {"id": "old", "name": name, "source_name": "id"} for name in ["x", "y"]])
    assert "old" not in {c.id for c in new.columns}
    assert _REMOVED_COLUMN in _build_column_migrations(node_columns_from_contract(old), node_columns_from_contract(new)).values()


@pytest.mark.parametrize("fmt", ["csv", "gdb", "xlsx", "json", "geojson", "gpkg", "shp"])
@pytest.mark.parametrize("streaming", [False, True])
def test_publisher_types_per_column_all_formats(fmt, streaming, monkeypatch):
    from app.core.projects import schema_execution_sources
    from app.core.estrai.executor import EstraiExecutor
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "sqlite")
    frame = pd.DataFrame({"geom": pd.Series(["POINT (1 2)"], dtype=object),
                          "day": pd.Series([date(2026, 11, 4)], dtype=object),
                          "amount": pd.Series([None], dtype=object)})
    declared = ["geometry", "date", "real"]
    canvas = canvas_of([{"id": "a", "type": "source", "table_name": "prima",
        "columns": [{"name": name, "type": kind} for name, kind in zip(frame.columns, declared)]},
        {"id": "r", "type": "result", "format": fmt}], [{"id": "ar", "source": "a", "target": "r"}])
    svc = service_for(canvas, "estrai").project_service
    svc.get_project_output_contract = lambda _: None
    svc.update_project_output_contract = lambda *args: None
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.core.query_execution.query_executor import QueryExecutor
    persistence = MagicMock()
    persistence.persist_dataframe_result.return_value = SimpleNamespace(
        result_id="result", storage_key="key", output_path=Path("result." + fmt))
    persistence.persist_streaming_query_to_storage.return_value = SimpleNamespace(
        result_id="result", storage_key="key", output_path=Path("result." + fmt),
        columns=list(frame.columns), rows_count=1)
    query = QueryExecutor(engine_factory=MagicMock(), result_persistence=persistence)
    query.execute_raw = MagicMock(return_value=(list(frame.itertuples(index=False, name=None)), list(frame.columns)))
    payload = query.execute_query(connection=SimpleNamespace(type="sqlite"), sql="SELECT geom, day, amount FROM prima",
        project_id="p", output_filename="result", output_format=fmt, geom_column=None if streaming else "geom")
    assert payload["success"] is True
    if streaming and fmt in {"csv", "xlsx"}:
        persistence.persist_streaming_query_to_storage.assert_called_once()
    else:
        persistence.persist_dataframe_result.assert_called_once()
        assert payload["column_dtypes"][0] in {"object", "str", "string"}
        assert payload["column_dtypes"][1:] == ["object"] * 2
    EstraiExecutor(svc, None, None, None)._salva_tipi_colonne_da_canvas("p", canvas, payload)
    assert [c["type"]["logical"] for c in payload["output_contract"]["columns"]] == ["geometry", "datetime", "number"]
    for plan in [[], [{"name": "amount", "type": "real"}]]:
        bound = result_columns_from_plan(project_id="p", columns=frame.columns, plan=plan, dataframe=frame)
        assert [c["type"] for c in bound[:2]] == ["geometry", "date"]


@pytest.mark.parametrize("dtype,declared,expected", [("int64", "geometry", "int"), ("float64", "str", "real"),
    ("bool", "date", "bool"), ("datetime64[ns]", "str", "date"), ("object", "geometry", "geometry")])
def test_specific_dtype_precedes_declaration(dtype, declared, expected):
    assert resolve_column_type(runtime_dtype=dtype, declared=declared) == expected


def test_invalid_destinations_follow_real_execution(monkeypatch):
    from app.core.projects import schema_execution_sources
    mapping = [{"path": "x", "column": ""}, {"path": "z", "column": None}]
    api = {"output_mapping": mapping, "options": {"rate_limit": 100000}}
    monkeypatch.setattr(schema_execution_sources, "load_api_schema_config", lambda _: api)
    canvas = canvas_of([{"id": "s", "type": "file_source", "columns": [{"name": "id", "type": "int"}]},
        {"id": "e", "type": "api_enrich", "data": {"api_id": "api"}}], [{"id": "se", "source": "s", "target": "e"}])
    _, frame = execute_mapping({}, api)
    response = service_for(canvas, "arricchisci").infer_schemas_response("p", canvas)
    assert [c["name"] for c in response["schemas"]["e"]] == [str(c) for c in frame.columns]
    assert response["schema_states"]["e"]["names_status"] == "indeterminate"


def test_mapping_access_is_centralized_ast():
    root = Path(__file__).parents[2] / "app" / "core" / "arricchisci"
    violations = []
    paths = list(root.glob("*.py")) + [root.parent / "projects/schema_inference.py",
        root.parents[2] / "workers/tasks/enrichment_task.py"]
    for path in paths:
        name = path.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if isinstance(node.func.value, ast.Name) and node.func.value.id in {"mapping", "m", "_m", "item"} and node.args:
                    if isinstance(node.args[0], ast.Constant) and node.args[0].value in {"column", "path"}:
                        violations.append((name, node.lineno))
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in {"mapping", "m", "_m", "item"}:
                if isinstance(node.slice, ast.Constant) and node.slice.value in {"column", "path"}:
                    violations.append((name, node.lineno))
    assert violations == []


def test_published_vocabulary_equals_consumer_vocabulary():
    allowed = set(get_args(OutputColumnType.model_fields["logical"].annotation))
    assert allowed == set(_LOGICAL_TYPE_COMPATIBLE_DTYPES)
    raw = ["unknown", "invented", None, "object", "str", "int", "float", "date", "bool", "geometry", "number"]
    contract = build_output_contract(project_id="p", result_columns=[{"name": str(i), "type": value} for i, value in enumerate(raw)])
    assert {c.type.logical for c in contract.columns} <= allowed
    assert [c.type.logical for c in contract.columns[:4]] == ["string"] * 4


def test_origin_rename_cannot_target_an_existing_physical_column():
    old = build_output_contract(project_id="p", result_columns=[
        {"name": "a", "source_name": "id", "source_node": "left", "id": "left-id"},
        {"name": "b", "source_name": "id", "source_node": "right", "id": "right-id"}])
    new = build_output_contract(project_id="p", previous_contract=old, result_columns=[
        {"name": "b", "source_name": "id", "source_node": "left", "id": "left-id"}])
    assert new.columns[0].id not in {c.id for c in old.columns}
    migrations = _build_column_migrations(node_columns_from_contract(old), node_columns_from_contract(new))
    assert all(value is _REMOVED_COLUMN for value in migrations.values())


def test_direct_contract_model_rejects_unknown():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OutputColumnType(logical="unknown")


def test_estrai_disambiguated_column_uses_plan_declaration(monkeypatch):
    from app.core.projects import schema_execution_sources
    from app.core.estrai.executor import EstraiExecutor
    from tests2.contracts.test_a2_schema_parity import sql_case
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "sqlite")
    canvas = sql_case("join")
    canvas.nodes[1].columns[0].type = "real"
    svc = service_for(canvas, "estrai").project_service
    svc.get_project_output_contract = lambda _: None
    svc.update_project_output_contract = lambda *args: None
    payload = {"columns": ["id", "valore", "id_2", "destra"],
               "column_types": ["str"] * 4, "column_dtypes": ["object"] * 4}
    EstraiExecutor(svc, None, None, None)._salva_tipi_colonne_da_canvas("p", canvas, payload)
    assert payload["output_contract"]["columns"][2]["type"]["logical"] == "number"
