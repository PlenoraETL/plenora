"""Anteprima della query di Estrai = query dell'esecuzione (Fase 5, T6, R3.13, N14).

Per ogni canvas del corpus l'anteprima (``describe_estrai_query``) restituisce
lo stesso SQL e gli stessi parametri del percorso dell'esecuzione
(``EstraiExecutor._execute_database``: ``query_plan.sql_query`` con le opzioni
della connessione). Il corpus copre le divergenze della raccolta: ORDER BY,
HAVING, tabelle non collegate, filtro 0, IN/BETWEEN, colonne calcolate con
alias, arco verso un Carica ArcGIS, filtri collegati a un input runtime, e il
percorso ArcGIS (layer al posto delle tabelle).
"""

from __future__ import annotations

import copy

import pytest

from app.core.estrai import query_plan
from app.core.estrai.query_generator import QueryGenerator
from app.core.projects.query_preview import describe_estrai_query
from app.core.projects.canvas_models import CanvasState

pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract]

SQL_OPTIONS = {"db_type": "postgres", "db_schema": "public"}


def _table(node_id, table, cols, **extra):
    return {"id": node_id, "type": "source", "position": {"x": 0, "y": 0}, "table_name": table,
            "columns": [{"name": c, "type": t, "visible": True} for c, t in cols], **extra}


def _result(**extra):
    return {"id": "out", "type": "result", "position": {"x": 0, "y": 0}, "fileName": "o", "format": "csv", **extra}


COMUNI = _table("c", "comuni", [("id", "int"), ("nome", "str"), ("pop", "int")])
PROVINCE = _table("p", "province", [("id", "int"), ("sigla", "str")])
JOIN = {"id": "j", "source": "p", "target": "c", "join_type": "left", "source_column": "id", "target_column": "id"}
TO_OUT = {"id": "o1", "source": "c", "target": "out", "join_type": "output"}

CASES = {
    "order_by": {"nodes": [COMUNI, _result(order_by_enabled=True, order_by_columns=[{"column": "nome", "direction": "DESC"}])],
                 "edges": [TO_OUT]},
    "having": {"nodes": [COMUNI, _result(group_by_enabled=True, group_by_columns=["nome"],
                                         aggregations=[{"column": "*", "function": "COUNT", "alias": "n"}],
                                         having_enabled=True,
                                         having_conditions=[{"function": "COUNT", "column": "*", "operator": ">", "value": "1"}])],
               "edges": [TO_OUT]},
    "tabella_scollegata": {"nodes": [COMUNI, PROVINCE, _result()], "edges": [TO_OUT]},
    "join": {"nodes": [COMUNI, PROVINCE, _result()], "edges": [JOIN, TO_OUT]},
    "filtro_zero": {"nodes": [{**COMUNI, "filters": [{"column": "pop", "operator": "=", "value": "0"}]}, _result()],
                    "edges": [TO_OUT]},
    "in_between": {"nodes": [{**COMUNI, "filters": [{"column": "id", "operator": "IN", "value": "1, 2"},
                                                    {"column": "pop", "operator": "BETWEEN", "value": "10 AND 20"}]},
                             _result()], "edges": [TO_OUT]},
    "colonna_calcolata": {"nodes": [{**COMUNI, "calculated_columns": [{"name": "doppio", "expression": "pop * 2"}]},
                                    _result()], "edges": [TO_OUT]},
    "carica_arcgis": {"nodes": [COMUNI, _result(), {"id": "ca", "type": "carica_arcgis", "position": {"x": 0, "y": 0}}],
                      "edges": [TO_OUT, {"id": "o2", "source": "out", "target": "ca", "join_type": "carica_arcgis"}]},
    "distinct_limit": {"nodes": [COMUNI, _result(distinct=True, limit=5)], "edges": [TO_OUT]},
}


def _describe(canvas_dict, *, sql_options=SQL_OPTIONS):
    return describe_estrai_query(
        CanvasState(**copy.deepcopy(canvas_dict)), project_id="p1", project_type="estrai",
        default_connection_id="conn", load_sql_options=lambda _cid: sql_options,
    )


def _execution(canvas_dict):
    canvas = CanvasState(**copy.deepcopy(canvas_dict))
    result = query_plan.estrai_result_node(canvas.nodes)
    return query_plan.sql_query(QueryGenerator(), canvas.nodes, canvas.edges, result, SQL_OPTIONS)


@pytest.mark.parametrize("name", CASES)
def test_preview_sql_is_the_execution_sql(name):
    preview = _describe(CASES[name])
    expected = _execution(CASES[name])
    assert preview["kind"] == "sql" and preview["error"] is None
    assert preview["sql"] == expected["sql"]
    assert {p["name"]: p["value"] for p in preview["params"]} == expected["params"]
    assert preview["tables_used"] == expected["tables_used"]


def test_audit_divergences_are_in_the_sql():
    assert "ORDER BY" in _describe(CASES["order_by"])["sql"]
    assert "HAVING" in _describe(CASES["having"])["sql"]
    assert "province" not in _describe(CASES["tabella_scollegata"])["sql"]
    zero = _describe(CASES["filtro_zero"])
    assert "WHERE" in zero["sql"] and [p["value"] for p in zero["params"]] == [0]
    in_between = _describe(CASES["in_between"])["sql"]
    assert "IN (:p_0, :p_1)" in in_between and "BETWEEN :p_2 AND :p_3" in in_between
    assert "AS doppio" in _describe(CASES["colonna_calcolata"])["sql"]
    assert "JOIN" not in _describe(CASES["carica_arcgis"])["sql"]


def test_filter_bound_to_runtime_input_is_shown_as_the_input():
    canvas = {
        "nodes": [
            {**COMUNI, "filters": [{"column": "nome", "operator": "=", "value": "statico"},
                                   {"column": "pop", "operator": "BETWEEN", "value": "1 AND 2"}]},
            _result(),
            {"id": "in1", "type": "project_input", "position": {"x": 0, "y": 0}, "input_key": "comune",
             "label": "Comune", "input_kind": "text", "required": True,
             "bindings": [{"target_node_id": "c", "target_field": "filters.0.value"}]},
            {"id": "in2", "type": "project_input", "position": {"x": 0, "y": 0}, "input_key": "fascia",
             "label": "Fascia", "input_kind": "text", "required": True,
             "bindings": [{"target_node_id": "c", "target_field": "filters.1.value"}]},
        ],
        "edges": [TO_OUT],
    }
    preview = _describe(canvas)
    assert preview["error"] is None
    by_name = {p["name"]: p for p in preview["params"]}
    assert by_name["p_0"]["input_key"] == "comune" and by_name["p_0"]["input_label"] == "Comune"
    assert by_name["p_0"]["value"] == "«Comune»"
    assert by_name["p_1"]["input_key"] == "fascia" and by_name["p_2"]["input_key"] == "fascia"
    assert all("statico" != p["value"] for p in preview["params"])


def test_arcgis_connection_shows_the_layer_read():
    layer = {"id": "fl", "type": "feature_source", "position": {"x": 0, "y": 0}, "layer_url": "https://x/FeatureServer/0",
             "where": "POP > 0", "geometry_type": "esriGeometryPoint",
             "columns": [{"name": "OBJECTID", "type": "int", "visible": True},
                         {"name": "NOME", "type": "str", "visible": False}]}
    canvas = {"nodes": [layer, _result(limit=10)], "edges": [{"id": "e", "source": "fl", "target": "out"}]}
    preview = describe_estrai_query(CanvasState(**canvas), project_id="p1", project_type="estrai",
                                    default_connection_id="arc", load_sql_options=lambda _cid: (_ for _ in ()).throw(ValueError("x")))
    assert preview["kind"] == "feature_service"
    plan = preview["feature_service"]
    assert plan["where"] == "POP > 0" and plan["out_fields"] == "OBJECTID" and plan["max_features"] == 10
    expected = query_plan.feature_service_query(CanvasState(**canvas).nodes, query_plan.estrai_result_node(CanvasState(**canvas).nodes))
    assert {k: plan[k] for k in ("layer_url", "where", "out_fields", "return_geometry", "max_features")} == \
        {k: expected[k] for k in ("layer_url", "where", "out_fields", "return_geometry", "max_features")}


def test_generation_error_is_reported_not_raised():
    preview = _describe({"nodes": [COMUNI, _result()], "edges": []})
    assert preview["kind"] == "sql" and preview["sql"] is None and preview["error"]


def test_executor_uses_the_same_plan():
    import inspect
    from app.core.estrai import executor
    source = inspect.getsource(executor.EstraiExecutor)
    assert "self.generator.generate_sql(" not in source
    assert source.count("query_plan_module.sql_query(") == 2
    assert "query_plan_module.feature_service_query(" in source
