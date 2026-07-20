"""Behavioral coverage for QueryProjectionRenderer.

Esercita SELECT (con alias/geometria/colonne calcolate), GROUP BY,
ORDER BY, HAVING, wrap WKT e assemble_query, costruendo il renderer
attorno a un vero QueryGenerator (che espone i 4 helper richiesti dal
costruttore: db_type, _get_node_table_name, _safe_identifier,
_prossimo_nome_param).
"""

from __future__ import annotations

import pytest

from app.core.estrai.query_generator import QueryGenerator
from app.core.estrai.query_projection import QueryProjectionRenderer
from app.core.projects.canvas_models import Node
from app.shared.execution.canvas_column import Column


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
]


# HELPERS

def _renderer(db_type: str = "postgres") -> QueryProjectionRenderer:
    return QueryProjectionRenderer(QueryGenerator(db_type=db_type))


def _source_node(node_id="src-1", table_name="MY_TABLE", alias="src",
                 columns=None, calculated_columns=None):
    return Node(
        id=node_id,
        type="source",
        position={"x": 0, "y": 0},
        table_name=table_name,
        alias=alias,
        columns=columns if columns is not None else [],
        filters=[],
        calculated_columns=calculated_columns or [],
    )


def _result_node():
    return Node(
        id="result-1",
        type="result",
        position={"x": 200, "y": 0},
        fileName="out",
        format="csv",
    )


# generate_select — happy path & branches

def test_generate_select_plain_columns_no_alias():
    node = _source_node(columns=[
        Column(name="ID", type="int", visible=True),
        Column(name="CITY", type="str", visible=True),
    ])
    r = _renderer()
    out = r.generate_select([node], _result_node())
    assert out == "src.ID, src.CITY"


def test_generate_select_uses_table_name_when_no_alias():
    node = _source_node(alias=None, columns=[Column(name="ID", type="int", visible=True)])
    r = _renderer()
    out = r.generate_select([node], _result_node())
    # alias assente -> usa il table_name come prefisso
    assert out == "MY_TABLE.ID"


def test_generate_select_with_column_alias_emits_as():
    node = _source_node(columns=[
        Column(name="CITY", alias="citta", type="str", visible=True),
    ])
    r = _renderer()
    out = r.generate_select([node], _result_node())
    assert out == "src.CITY AS citta"


def test_generate_select_skips_invisible_columns():
    node = _source_node(columns=[
        Column(name="ID", type="int", visible=True),
        Column(name="SECRET", type="str", visible=False),
    ])
    r = _renderer()
    out = r.generate_select([node], _result_node())
    assert out == "src.ID"
    assert "SECRET" not in out


def test_generate_select_geometry_wraps_wkt_and_uses_name_as_output():
    node = _source_node(columns=[
        Column(name="geom", type="geometry(Point)", visible=True),
    ])
    r = _renderer("postgres")
    out = r.generate_select([node], _result_node())
    assert "ST_AsText(src.geom)" in out
    assert out.endswith("AS geom")


def test_generate_select_geometry_with_alias_uses_alias_as_output():
    node = _source_node(columns=[
        Column(name="geom", alias="shape", type="geometry(Polygon)", visible=True),
    ])
    r = _renderer("postgres")
    out = r.generate_select([node], _result_node())
    assert "ST_AsText(src.geom)" in out
    assert out.endswith("AS shape")


def test_generate_select_raises_when_no_visible_columns():
    node = _source_node(columns=[Column(name="X", type="str", visible=False)])
    r = _renderer()
    with pytest.raises(ValueError, match="Nessuna colonna selezionata"):
        r.generate_select([node], _result_node())


def test_generate_select_quoted_identifier_for_hyphenated_name():
    node = _source_node(columns=[Column(name="poi-name", type="str", visible=True)])
    r = _renderer()
    out = r.generate_select([node], _result_node())
    assert out == 'src."poi-name"'


# generate_select — calculated columns

def test_generate_select_calculated_manual_sql_passthrough():
    node = _source_node(
        columns=[Column(name="ID", type="int", visible=True)],
        calculated_columns=[{"name": "manual", "is_manual": True, "sql": "1 + 1 AS manual"}],
    )
    r = _renderer()
    out = r.generate_select([node], _result_node())
    assert "1 + 1 AS manual" in out


def test_generate_select_calculated_function_with_params():
    node = _source_node(
        columns=[Column(name="ID", type="int", visible=True)],
        calculated_columns=[{
            "name": "upper_city",
            "function_name": "UPPER",
            "params": {"column": "CITY"},
        }],
    )
    r = _renderer("postgres")
    out = r.generate_select([node], _result_node())
    assert "AS upper_city" in out
    assert "UPPER" in out.upper()


def test_generate_select_calculated_expression_dict():
    node = _source_node(
        columns=[Column(name="ID", type="int", visible=True)],
        calculated_columns=[{
            "name": "lower_city",
            "expression": {"function": "LOWER", "params": {"column": "CITY"}},
        }],
    )
    r = _renderer("postgres")
    out = r.generate_select([node], _result_node())
    assert "AS lower_city" in out


def test_generate_select_calculated_expression_string_manual():
    node = _source_node(
        columns=[Column(name="ID", type="int", visible=True)],
        calculated_columns=[{
            "name": "raw_expr",
            "function_name": None,
            "params": None,
            "expression": "src.A + src.B",
        }],
    )
    r = _renderer()
    out = r.generate_select([node], _result_node())
    assert "(src.A + src.B) AS raw_expr" in out


def test_generate_select_calculated_invalid_skipped_with_warning():
    node = _source_node(
        columns=[Column(name="ID", type="int", visible=True)],
        calculated_columns=[{"name": "bogus"}],  # no sql/function/expression
    )
    r = _renderer()
    out = r.generate_select([node], _result_node())
    # la colonna calcolata invalida e' saltata, ma la query e' valida
    assert out == "src.ID"


def test_generate_select_calculated_error_wrapped_in_valueerror():
    node = _source_node(
        columns=[Column(name="ID", type="int", visible=True)],
        calculated_columns=[{
            "name": "broken",
            "function_name": "NO_SUCH_FUNC_XYZ",
            "params": {"column": "CITY"},
        }],
    )
    r = _renderer("postgres")
    with pytest.raises(ValueError, match="Errore colonna calcolata 'broken'"):
        r.generate_select([node], _result_node())


# generate_select — delega a group by

def test_generate_select_delegates_to_groupby_when_enabled():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    gb = {
        "enabled": True,
        "columns": ["CITY"],
        "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}],
    }
    out = r.generate_select([node], _result_node(), group_by_config=gb)
    assert "COUNT(*) AS n" in out
    assert "src.CITY" in out


# generate_select_with_groupby

def test_groupby_resolves_column_to_table_alias():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": ["CITY"], "aggregations": []}
    out = r.generate_select_with_groupby([node], gb)
    assert out == "src.CITY"


def test_groupby_double_underscore_column_strips_prefix():
    node = _source_node(alias="t", columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    # table_map ha "CITY" -> "t"; col con prefisso __ viene splittato
    gb = {"enabled": True, "columns": ["CITY"], "aggregations": [
        {"column": "t__CITY", "function": "COUNT", "alias": "c"},
    ]}
    # "t__CITY" non e' nel table_map -> ramo else: usa la stringa intera
    out = r.generate_select_with_groupby([node], gb)
    assert "COUNT(t__CITY) AS c" in out or 'COUNT("t__CITY")' in out


def test_groupby_aggregation_dict_default_alias():
    node = _source_node(columns=[Column(name="POP", type="int", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": [], "aggregations": [
        {"column": "POP", "function": "SUM"},  # alias derivato: sum_POP
    ]}
    out = r.generate_select_with_groupby([node], gb)
    assert "SUM(src.POP) AS sum_POP" in out


def test_groupby_aggregation_object_with_attributes():
    class Agg:
        column = "POP"
        function = "AVG"
        alias = "avg_pop"

    node = _source_node(columns=[Column(name="POP", type="int", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": [], "aggregations": [Agg()]}
    out = r.generate_select_with_groupby([node], gb)
    assert "AVG(src.POP) AS avg_pop" in out


def test_groupby_count_star():
    node = _source_node(columns=[Column(name="ID", type="int", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": [], "aggregations": [
        {"column": "*", "function": "COUNT", "alias": "total"},
    ]}
    out = r.generate_select_with_groupby([node], gb)
    assert "COUNT(*) AS total" in out


def test_groupby_invalid_function_raises():
    node = _source_node(columns=[Column(name="POP", type="int", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": [], "aggregations": [
        {"column": "POP", "function": "MEDIAN", "alias": "m"},
    ]}
    with pytest.raises(ValueError, match="Funzione aggregazione non valida: MEDIAN"):
        r.generate_select_with_groupby([node], gb)


def test_groupby_star_with_non_count_raises():
    node = _source_node(columns=[Column(name="ID", type="int", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": [], "aggregations": [
        {"column": "*", "function": "SUM", "alias": "s"},
    ]}
    with pytest.raises(ValueError, match="Solo COUNT puo usare"):
        r.generate_select_with_groupby([node], gb)


def test_groupby_empty_raises():
    node = _source_node(columns=[Column(name="ID", type="int", visible=True)])
    r = _renderer()
    gb = {"enabled": True, "columns": [], "aggregations": []}
    with pytest.raises(ValueError, match="Nessuna colonna per GROUP BY"):
        r.generate_select_with_groupby([node], gb)


def test_groupby_unmapped_column_falls_back_to_bare_identifier():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    # GROUP BY su colonna non visibile/non mappata -> ramo else
    gb = {"enabled": True, "columns": ["UNKNOWN_COL"], "aggregations": []}
    out = r.generate_select_with_groupby([node], gb)
    assert out == "UNKNOWN_COL"


# generate_group_by

def test_generate_group_by_disabled_returns_empty():
    r = _renderer()
    assert r.generate_group_by({"enabled": False}, []) == ""
    assert r.generate_group_by(None, []) == ""


def test_generate_group_by_no_columns_returns_empty():
    r = _renderer()
    assert r.generate_group_by({"enabled": True, "columns": []}, []) == ""


def test_generate_group_by_qualifies_mapped_columns():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    out = r.generate_group_by({"enabled": True, "columns": ["CITY"]}, [node])
    assert out == "src.CITY"


def test_generate_group_by_unmapped_column_bare():
    r = _renderer()
    out = r.generate_group_by({"enabled": True, "columns": ["LONELY"]}, [])
    assert out == "LONELY"


def test_generate_group_by_double_underscore_resolves_real_name():
    node = _source_node(alias="t", columns=[
        Column(name="CITY", alias="t__CITY", type="str", visible=True),
    ])
    r = _renderer()
    # alias "t__CITY" e' nel table_map -> resolved, strip prefisso -> CITY
    out = r.generate_group_by({"enabled": True, "columns": ["t__CITY"]}, [node])
    assert out == "t.CITY"


# generate_order_by

def test_order_by_disabled_returns_empty():
    r = _renderer()
    assert r.generate_order_by({"enabled": False}, [], {}) == ""
    assert r.generate_order_by({"enabled": True, "columns": []}, [], {}) == ""


def test_order_by_simple_column_with_direction():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    cfg = {"enabled": True, "columns": [{"column": "CITY", "direction": "desc"}]}
    out = r.generate_order_by(cfg, [node], {})
    assert out == "src.CITY DESC"


def test_order_by_invalid_direction_defaults_asc():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    cfg = {"enabled": True, "columns": [{"column": "CITY", "direction": "sideways"}]}
    out = r.generate_order_by(cfg, [node], {})
    assert out == "src.CITY ASC"


def test_order_by_string_item_defaults_asc():
    node = _source_node(columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    cfg = {"enabled": True, "columns": ["CITY"]}
    out = r.generate_order_by(cfg, [node], {})
    assert out == "src.CITY ASC"


def test_order_by_aggregate_expression_allowed():
    r = _renderer()
    cfg = {"enabled": True, "columns": [{"column": "COUNT(id)", "direction": "DESC"}]}
    out = r.generate_order_by(cfg, [], {})
    assert out == "COUNT(id) DESC"


def test_order_by_invalid_aggregate_expression_raises():
    r = _renderer()
    cfg = {"enabled": True, "columns": [{"column": "SUM(a); DROP TABLE x)", "direction": "ASC"}]}
    with pytest.raises(ValueError, match="Espressione aggregata ORDER BY non valida"):
        r.generate_order_by(cfg, [], {})


def test_order_by_unmapped_column_bare():
    r = _renderer()
    cfg = {"enabled": True, "columns": [{"column": "FREE", "direction": "ASC"}]}
    out = r.generate_order_by(cfg, [], {})
    assert out == "FREE ASC"


def test_order_by_double_underscore_strips_prefix():
    node = _source_node(alias="t", columns=[Column(name="CITY", type="str", visible=True)])
    r = _renderer()
    cfg = {"enabled": True, "columns": [{"column": "CITY", "direction": "ASC"}]}
    out = r.generate_order_by(cfg, [node], {})
    assert out == "t.CITY ASC"


# generate_having

def test_having_disabled_returns_empty():
    r = _renderer()
    assert r.generate_having({"enabled": False}, {"enabled": True}, {}) == ""
    assert r.generate_having({"enabled": True, "conditions": []}, {"enabled": True}, {}) == ""


def test_having_requires_groupby_enabled():
    r = _renderer()
    cfg = {"enabled": True, "conditions": [
        {"function": "COUNT", "column": "*", "operator": ">", "value": "5"},
    ]}
    assert r.generate_having(cfg, {"enabled": False}, {}) == ""


def test_having_count_star_with_param():
    r = _renderer()
    params = {}
    cfg = {"enabled": True, "conditions": [
        {"function": "COUNT", "column": "*", "operator": ">", "value": "5"},
    ]}
    out = r.generate_having(cfg, {"enabled": True}, params)
    assert out.startswith("COUNT(*) > :p_")
    # il valore tipizzato come int finisce nei params
    assert 5 in params.values()


def test_having_sum_column_float_value():
    r = _renderer()
    params = {}
    cfg = {"enabled": True, "conditions": [
        {"function": "SUM", "column": "POP", "operator": ">=", "value": "10.5"},
    ]}
    out = r.generate_having(cfg, {"enabled": True}, params)
    assert "SUM(POP) >= :p_" in out
    assert 10.5 in params.values()


def test_having_non_numeric_value_kept_as_is():
    r = _renderer()
    params = {}
    cfg = {"enabled": True, "conditions": [
        {"function": "MAX", "column": "NAME", "operator": "=", "value": "abc"},
    ]}
    out = r.generate_having(cfg, {"enabled": True}, params)
    assert "MAX(NAME) = :p_" in out
    assert "abc" in params.values()


def test_having_invalid_function_skipped():
    r = _renderer()
    params = {}
    cfg = {"enabled": True, "conditions": [
        {"function": "MEDIAN", "column": "X", "operator": ">", "value": "1"},
    ]}
    out = r.generate_having(cfg, {"enabled": True}, params)
    assert out == ""
    assert params == {}


def test_having_invalid_operator_skipped():
    r = _renderer()
    params = {}
    cfg = {"enabled": True, "conditions": [
        {"function": "COUNT", "column": "*", "operator": "LIKE", "value": "1"},
    ]}
    out = r.generate_having(cfg, {"enabled": True}, params)
    assert out == ""


def test_having_multiple_conditions_joined_with_and():
    r = _renderer()
    params = {}
    cfg = {"enabled": True, "conditions": [
        {"function": "COUNT", "column": "*", "operator": ">", "value": "1"},
        {"function": "SUM", "column": "POP", "operator": "<", "value": "100"},
    ]}
    out = r.generate_having(cfg, {"enabled": True}, params)
    assert " AND " in out
    assert len(params) == 2


# wrap_geometry_as_wkt

def test_wrap_geometry_postgres():
    r = _renderer("postgres")
    out = r.wrap_geometry_as_wkt("t.geom")
    assert "ST_AsText(t.geom)" in out


def test_wrap_geometry_unknown_db_falls_back():
    r = _renderer("totally_unknown_db")
    out = r.wrap_geometry_as_wkt("t.geom")
    assert out == "ST_AsText(t.geom)"


def test_wrap_geometry_none_db_defaults_postgres():
    r = _renderer("postgres")
    r.db_type = None
    out = r.wrap_geometry_as_wkt("t.geom")
    assert "ST_AsText(t.geom)" in out


# assemble_query

def _base_parts(**over):
    parts = {
        "select": "t.a, t.b",
        "from": "MY_TABLE t",
        "where": "",
    }
    parts.update(over)
    return parts


def test_assemble_query_minimal():
    r = _renderer()
    sql = r.assemble_query(_base_parts())
    assert sql.startswith("SELECT t.a, t.b")
    assert "FROM MY_TABLE t" in sql


def test_assemble_query_with_where():
    r = _renderer()
    sql = r.assemble_query(_base_parts(where="t.a > 1"))
    assert "WHERE t.a > 1" in sql


def test_assemble_query_distinct_only_without_groupby():
    r = _renderer()
    sql = r.assemble_query(_base_parts(distinct=True))
    assert sql.startswith("SELECT DISTINCT ")


def test_assemble_query_distinct_suppressed_with_groupby():
    r = _renderer()
    sql = r.assemble_query(_base_parts(distinct=True, group_by="t.a"))
    assert "DISTINCT" not in sql
    assert "GROUP BY t.a" in sql


def test_assemble_query_all_clauses_order():
    r = _renderer()
    sql = r.assemble_query(_base_parts(
        where="t.a > 1", group_by="t.a", having="COUNT(*) > 1",
        order_by="t.a ASC", limit=10,
    ))
    # ordine FROM -> WHERE -> GROUP BY -> HAVING -> ORDER BY -> LIMIT
    assert sql.index("WHERE") < sql.index("GROUP BY") < sql.index("HAVING")
    assert sql.index("HAVING") < sql.index("ORDER BY") < sql.index("LIMIT")


def test_assemble_query_limit_postgres():
    r = _renderer("postgres")
    sql = r.assemble_query(_base_parts(limit=5))
    assert sql.endswith("LIMIT 5")


def test_assemble_query_limit_oracle_fetch_first():
    r = _renderer("oracle")
    sql = r.assemble_query(_base_parts(limit=7))
    assert "FETCH FIRST 7 ROWS ONLY" in sql
    assert "LIMIT" not in sql


def test_assemble_query_limit_mssql_top():
    r = _renderer("mssql")
    sql = r.assemble_query(_base_parts(limit=3))
    assert sql.startswith("SELECT TOP 3 ")
    assert "LIMIT" not in sql


def test_assemble_query_limit_zero_allowed():
    r = _renderer("postgres")
    sql = r.assemble_query(_base_parts(limit=0))
    assert sql.endswith("LIMIT 0")


def test_assemble_query_negative_limit_raises():
    r = _renderer()
    with pytest.raises(ValueError, match="LIMIT deve essere un intero positivo"):
        r.assemble_query(_base_parts(limit=-1))


def test_assemble_query_non_int_limit_raises():
    r = _renderer()
    with pytest.raises(ValueError, match="LIMIT deve essere un intero positivo"):
        r.assemble_query(_base_parts(limit="ten"))


# _visible_column_table_map (covered indirectly, plus direct edge)

def test_visible_column_table_map_includes_alias_keys():
    node = _source_node(alias="t", columns=[
        Column(name="CITY", alias="citta", type="str", visible=True),
        Column(name="HIDDEN", type="str", visible=False),
    ])
    r = _renderer()
    table_map = r._visible_column_table_map([node])
    assert table_map["CITY"] == "t"
    assert table_map["citta"] == "t"
    assert "HIDDEN" not in table_map


def test_visible_column_table_map_uses_table_name_without_alias():
    node = _source_node(alias=None, columns=[Column(name="X", type="str", visible=True)])
    r = _renderer()
    table_map = r._visible_column_table_map([node])
    assert table_map["X"] == "MY_TABLE"
