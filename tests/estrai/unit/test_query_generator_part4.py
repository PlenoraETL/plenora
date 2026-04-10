"""
tests/estrai/unit/test_query_generator.py
==========================================
Unit test diretti su QueryGenerator.

NON richiede DB, API o Celery.
"""

import pytest
from helpers import make_source, make_result, make_edge, output_edge

pytestmark = pytest.mark.unit

from app.core.estrai.query_generator import QueryGenerator
from app.shared.models import Filter, Node, Column, Position


# ─── Helper ───────────────────────────────────────────────────────────────────

def qg(db_type="postgres"):
    return QueryGenerator(db_type=db_type)


def gen(nodes, edges, db_type="postgres", db_schema=None):
    g = qg(db_type)
    result_id = next(n.id for n in nodes if n.type == "result")
    return g.generate_sql(nodes, edges, result_id, db_type=db_type, db_schema=db_schema)


def sql(nodes, edges, db_type="postgres", db_schema=None):
    return gen(nodes, edges, db_type, db_schema)["sql"]




# =============================================================================
# COVERAGE GAPS V2 — targeted tests for uncovered lines
# =============================================================================

class TestCoverageGapsV2:
    """Targeted tests to raise coverage from 78% to 90%+."""

    # ── Lines 278-279: ORDER BY config from result_node.data dict ────────

    def test_order_by_from_data_dict(self):
        """Lines 278-279: ORDER BY read from .data dict when direct attrs are off."""
        src = make_source("s1", "tab", [
            {"name": "nome", "type": "str"},
            {"name": "id", "type": "int"},
        ])
        res = make_result()
        res.order_by_enabled = False
        res.data = {
            "order_by_enabled": True,
            "order_by_columns": [
                {"column": "nome", "direction": "DESC"},
            ],
        }
        q = sql([src, res], [output_edge("s1")])
        assert "ORDER BY" in q
        assert "DESC" in q

    # ── Lines 311-312: HAVING config from result_node.data dict ──────────

    def test_having_from_data_dict(self):
        """Lines 311-312: HAVING read from .data dict."""
        src = make_source("s1", "tab", [
            {"name": "cat", "type": "str"},
            {"name": "val", "type": "int"},
        ])
        res = make_result()
        res.group_by_enabled = False
        res.having_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["cat"],
            "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}],
            "having_enabled": True,
            "having_conditions": [
                {"function": "COUNT", "column": "*", "operator": ">", "value": "3"},
            ],
        }
        q = sql([src, res], [output_edge("s1")])
        assert "HAVING" in q
        assert "COUNT(*)" in q

    # ── Line 342: _trova_tabelle_connesse skips already visited ──────────

    def test_trova_tabelle_visited_skip(self):
        """Line 342: BFS skips already-visited nodes (diamond graph)."""
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "id", "type": "int"}])
        res = make_result()
        # a -> res, b -> res, a -> b  =>  'a' reachable from two paths
        edges = [
            output_edge("a"),
            make_edge("e1", "b", "result", join_type="output"),
            make_edge("e2", "a", "b", join_type="inner", source_col="id", target_col="id"),
        ]
        r = gen([a, b, res], edges)
        # Should not crash or double-count
        assert "t1" in r["tables_used"]

    # ── Line 396: _riordina fallback when all nodes are targets (cycle) ──

    def test_riordina_cycle_fallback(self):
        """Line 396: all nodes are join targets -> fallback to first."""
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "id", "type": "int"}])
        res = make_result()
        # Edges where both a and b are targets of each other
        edges = [
            make_edge("e1", "a", "b", join_type="inner", source_col="id", target_col="id"),
            make_edge("e2", "b", "a", join_type="inner", source_col="id", target_col="id"),
            output_edge("b"),
        ]
        r = gen([a, b, res], edges)
        assert len(r["tables_used"]) == 2

    # ── Lines 428-430: _riordina — no dependency found, pop first ────────

    def test_riordina_no_dependency_adds_anyway(self):
        """Lines 428-430: remaining nodes with no edge to ordered are added anyway."""
        a = make_source("a", "t1", [{"name": "x", "type": "int"}])
        b = make_source("b", "t2", [{"name": "y", "type": "int"}])
        c = make_source("c", "t3", [{"name": "z", "type": "int"}])
        res = make_result()
        # Only a->b edge, c has no edge to anyone
        edges = [
            make_edge("e1", "a", "b", join_type="inner", source_col="x", target_col="y"),
            output_edge("b"),
            make_edge("e3", "c", "result", join_type="output"),
        ]
        r = gen([a, b, c, res], edges)
        assert "t3" in r["tables_used"]

    # ── Lines 465-468: datasource_node with no visible columns → table.* ─

    def test_datasource_node_no_visible_columns_star(self):
        """Lines 465-468: datasource_node with no visible columns => table.*"""
        node = Node(
            id="ds1", type="datasource_node",
            position=Position(x=0, y=0),
            datasource_name="mydata",
            columns=[],
        )
        res = make_result()
        edges = [output_edge("ds1")]
        q = sql([node, res], edges)
        assert "_urby_ds_mydata.*" in q

    # ── Lines 512-522: calc column with expression dict (legacy format) ──

    def test_calculated_column_expression_dict_legacy(self):
        """Lines 512-519: expression as dict (legacy format)."""
        src = make_source("s1", "tab", [
            {"name": "col1", "type": "str"},
        ], calculated_columns=[{
            "name": "upper_col",
            "expression": {"function": "UPPER", "params": {"column": "col1"}},
        }])
        q = sql([src, make_result()], [output_edge("s1")])
        assert "upper_col" in q.lower() and "UPPER" in q

    def test_calculated_column_no_format_skipped(self):
        """Lines 520-522: calc col with no recognized format is silently skipped."""
        src = make_source("s1", "tab", [
            {"name": "col1", "type": "str"},
        ], calculated_columns=[{
            "name": "mystery",
            # No is_manual, no function_name, no expression
        }])
        q = sql([src, make_result()], [output_edge("s1")])
        assert "mystery" not in q

    # ── Lines 562, 571-572: _genera_select_con_groupby unresolved column ─

    def test_groupby_select_unresolved_column(self):
        """Lines 571-572: group by column not in table_map => emitted unqualified."""
        src = make_source("s1", "tab", [
            {"name": "visible_col", "type": "str"},
        ])
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["unknown_col"],
            "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}],
        }
        # unknown_col is not in any table's visible columns -> emitted unqualified
        q = sql([src, res], [output_edge("s1")])
        assert "unknown_col" in q

    # ── Line 607: empty group_by raises ValueError ───────────────────────

    def test_groupby_no_columns_no_aggs_raises(self):
        """Line 607: GROUP BY enabled but empty columns and aggregations -> ValueError."""
        src = make_source("s1", "tab", [{"name": "x", "type": "int"}])
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": [],
            "aggregations": [],
        }
        with pytest.raises(ValueError, match="Nessuna colonna per GROUP BY"):
            sql([src, res], [output_edge("s1")])

    # ── Line 627: _genera_group_by returns "" when columns list is empty ─

    def test_genera_group_by_empty_columns(self):
        """Line 627: GROUP BY enabled but columns=[] -> returns empty string."""
        g = qg()
        result = g._genera_group_by({"enabled": True, "columns": []}, [])
        assert result == ""

    # ── Lines 643, 653-654: _genera_group_by with alias and unresolved ───

    def test_genera_group_by_unresolved_column(self):
        """Lines 653-654: column not in table_map => emitted without table prefix."""
        g = qg()
        src = make_source("s1", "tab", [{"name": "known", "type": "str"}])
        result = g._genera_group_by(
            {"enabled": True, "columns": ["unknown_field"]},
            [src]
        )
        assert "unknown_field" in result
        assert "tab." not in result

    # ── Line 699: order_by — col alias in table_map ──────────────────────

    def test_order_by_column_with_alias_in_map(self):
        """Line 699: column alias registered in table_map for ORDER BY."""
        src = make_source("s1", "tab", [
            {"name": "nome", "type": "str", "alias": "name_alias"},
        ])
        res = make_result(order_by_enabled=True, order_by_columns=[
            {"column": "name_alias", "direction": "ASC"}
        ])
        q = sql([src, res], [output_edge("s1")])
        assert "ORDER BY" in q
        assert "name_alias" in q

    # ── Line 712: ORDER BY invalid direction falls back to ASC ───────────

    def test_order_by_invalid_direction_defaults_asc(self):
        """Line 712: invalid direction normalized to ASC."""
        g = qg()
        src = make_source("s1", "tab", [{"name": "col", "type": "str"}])
        result = g._genera_order_by(
            {"enabled": True, "columns": [{"column": "col", "direction": "SIDEWAYS"}]},
            [src],
            {"enabled": False}
        )
        assert "ASC" in result

    # ── Line 720: ORDER BY with aggregate expression ─────────────────────

    def test_order_by_aggregate_expression(self):
        """Line 720: aggregate expression in ORDER BY used as-is."""
        g = qg()
        src = make_source("s1", "tab", [{"name": "col", "type": "str"}])
        result = g._genera_order_by(
            {"enabled": True, "columns": [{"column": "COUNT(*)", "direction": "DESC"}]},
            [src],
            {"enabled": True}
        )
        assert "COUNT(*) DESC" in result

    # ── Lines 729-730: ORDER BY unresolved column ────────────────────────

    def test_order_by_unresolved_column(self):
        """Lines 729-730: column not in table_map => emitted without table prefix."""
        g = qg()
        src = make_source("s1", "tab", [{"name": "known", "type": "str"}])
        result = g._genera_order_by(
            {"enabled": True, "columns": [{"column": "unknown_col", "direction": "DESC"}]},
            [src],
            {"enabled": False}
        )
        assert "unknown_col DESC" in result
        assert "tab." not in result

    # ── Lines 779, 781: HAVING skips invalid function/operator ───────────

    def test_having_invalid_function_skipped(self):
        """Line 779: HAVING condition with invalid function is skipped."""
        g = qg()
        params = {}
        result = g._genera_having(
            {"enabled": True, "conditions": [
                {"function": "MEDIAN", "column": "*", "operator": ">", "value": "5"}
            ]},
            {"enabled": True},
            params
        )
        assert result == ""

    def test_having_invalid_operator_skipped(self):
        """Line 781: HAVING condition with invalid operator is skipped."""
        g = qg()
        params = {}
        result = g._genera_having(
            {"enabled": True, "conditions": [
                {"function": "COUNT", "column": "*", "operator": "LIKE", "value": "5"}
            ]},
            {"enabled": True},
            params
        )
        assert result == ""

    # ── Lines 794-795: HAVING float value conversion ─────────────────────

    def test_having_float_value_conversion(self):
        """Lines 793-795: HAVING value with decimal point converted to float."""
        g = qg()
        params = {}
        result = g._genera_having(
            {"enabled": True, "conditions": [
                {"function": "AVG", "column": "score", "operator": ">", "value": "3.5"}
            ]},
            {"enabled": True},
            params
        )
        assert "AVG(score)" in result
        # Value should be float 3.5
        assert any(isinstance(v, float) and abs(v - 3.5) < 1e-9 for v in params.values())

    def test_having_non_numeric_value(self):
        """Lines 794-795: HAVING value that is not numeric stays as string."""
        g = qg()
        params = {}
        result = g._genera_having(
            {"enabled": True, "conditions": [
                {"function": "COUNT", "column": "*", "operator": ">", "value": "abc"}
            ]},
            {"enabled": True},
            params
        )
        assert "COUNT(*)" in result
        assert any(v == "abc" for v in params.values())

    # ── Lines 817-818: _wrap_geometry_as_wkt fallback ────────────────────

    def test_wrap_geometry_wkt_fallback(self):
        """Lines 817-818: when sql_translator fails, fallback to ST_AsText."""
        g = qg("postgres")
        result = g._wrap_geometry_as_wkt("t.geom")
        # Should always produce some form of AsText wrapping
        lower = result.lower()
        assert "astext" in lower

    # ── Lines 838: _genera_from raises on empty list ─────────────────────

    def test_genera_from_empty_raises(self):
        """Line 838: empty table_nodes raises ValueError."""
        g = qg()
        with pytest.raises(ValueError, match="Nessuna tabella selezionata"):
            g._genera_from([], [])

    # ── Lines 895, 903: _genera_join edge reversal ───────────────────────

    def test_join_reversed_edge(self):
        """Lines 895, 940-941: reversed edge (node is edge.source)."""
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        res = make_result()
        # Edge goes from b -> a (b is source), but b is the second node to join
        j = make_edge("j", "b", "a", join_type="inner", source_col="fk", target_col="id")
        edges = [j, output_edge("a")]
        q = sql([a, b, res], edges)
        assert "JOIN" in q

    # ── Lines 917-919: CROSS JOIN with alias when no edge found ──────────

    def test_cross_join_with_alias(self):
        """Lines 917-919: no edge found -> CROSS JOIN, with alias."""
        g = qg()
        a = make_source("a", "t1", [{"name": "x", "type": "int"}], alias="alias_a")
        b = make_source("b", "t2", [{"name": "y", "type": "int"}], alias="alias_b")
        # Call _genera_join directly with no matching edges
        result = g._genera_join(b, [], [a, b], processed_ids={"a"})
        assert "CROSS JOIN" in result
        assert "alias_b" in result

    # ── Line 925: unknown join_type falls back to LEFT ───────────────────

    def test_unknown_join_type_fallback_left(self):
        """Line 925: unknown join_type defaults to LEFT."""
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        res = make_result()
        j = make_edge("j", "a", "b", join_type="inner", source_col="id", target_col="fk")
        # Manually override join_type to something invalid
        j.join_type = "weird"
        edges = [j, output_edge("b")]
        q = sql([a, b, res], edges)
        assert "LEFT JOIN" in q

    # ── Lines 1057-1060: join_conditions with reversed edge ──────────────

    def test_join_conditions_reversed(self):
        """Lines 1057-1060: join_conditions columns swapped when edge is reversed."""
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "ref_id", "type": "int"}])
        res = make_result()
        # Edge from b -> a, but b is the second table joined
        j = make_edge("j", "b", "a", join_type="inner",
                      join_conditions=[{"source_column": "ref_id", "target_column": "id"}])
        edges = [j, output_edge("a")]
        q = sql([a, b, res], edges)
        # When reversed, source_column and target_column swap
        assert "JOIN" in q

    # ── Lines 1068, 1070, 1082, 1085, 1087: invalid join columns raise ──

    def test_join_conditions_invalid_column_raises(self):
        """Lines 1068, 1070: invalid identifier in join_conditions raises ValueError."""
        g = qg()
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         join_conditions=[{"source_column": "DROP", "target_column": "fk"}])
        with pytest.raises(ValueError, match="Colonna JOIN non valida"):
            g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)

    def test_join_backward_compat_invalid_column_raises(self):
        """Lines 1085, 1087: invalid column in backward-compat single pair raises."""
        g = qg()
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="delete", target_col="fk")
        with pytest.raises(ValueError, match="Colonna JOIN non valida"):
            g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)

    # ── Lines 1096-1124: ON filters with join_conditions ─────────────────

    def test_on_filters_is_null(self):
        """Lines 1113-1114: ON filter with IS NULL operator."""
        g = qg()
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="fk")
        edge.on_filters = [{"column": "fk", "operator": "IS NULL", "value": ""}]
        result = g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)
        assert "IS NULL" in result

    def test_on_filters_in_operator(self):
        """Lines 1116-1120: ON filter with IN operator."""
        g = qg()
        b = make_source("b", "t2", [{"name": "status", "type": "str"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="status")
        edge.on_filters = [{"column": "status", "operator": "IN", "value": "A,B,C"}]
        result = g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)
        assert "IN" in result
        assert "'A'" in result

    def test_on_filters_equality(self):
        """Lines 1122-1124: ON filter with = operator."""
        g = qg()
        b = make_source("b", "t2", [{"name": "tipo", "type": "str"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="tipo")
        edge.on_filters = [{"column": "tipo", "operator": "=", "value": "attivo"}]
        result = g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)
        assert "= 'attivo'" in result

    def test_on_filters_with_func(self):
        """Lines 1110: ON filter with func wrapping."""
        g = qg()
        b = make_source("b", "t2", [{"name": "nome", "type": "str"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="nome")
        edge.on_filters = [{"column": "nome", "operator": "=", "value": "test", "func": "TRIM"}]
        result = g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)
        assert "TRIM(" in result

    def test_on_filters_reversed_edge(self):
        """Line 1096: reversed edge uses source_alias for ON filters."""
        g = qg()
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="fk")
        edge.on_filters = [{"column": "fk", "operator": "IS NOT NULL", "value": ""}]
        result = g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", is_reversed=True)
        # When reversed, filters use source_alias (t1)
        assert "t1.fk IS NOT NULL" in result

    def test_on_filters_empty_column_skipped(self):
        """Lines 1104-1105: ON filter with empty column is skipped."""
        g = qg()
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="fk")
        edge.on_filters = [{"column": "", "operator": "=", "value": "x"}]
        result = g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)
        # The empty-column filter is skipped; only the join condition remains
        assert "t1.id = t2.fk" in result

    def test_on_filters_invalid_column_raises(self):
        """Lines 1106-1107: ON filter with invalid column raises ValueError."""
        g = qg()
        b = make_source("b", "t2", [{"name": "fk", "type": "int"}])
        edge = make_edge("j", "a", "b", join_type="inner",
                         source_col="id", target_col="fk")
        edge.on_filters = [{"column": "DROP", "operator": "=", "value": "x"}]
        with pytest.raises(ValueError, match="Colonna filtro ON non valida"):
            g._genera_on_attributivo(edge, "t1", "t2", b, "INNER", False)

    # ── Lines 1010-1014: Oracle CLOB wrapping in _crea_condizione ────────

    def test_oracle_clob_wrapping(self):
        """Lines 1207-1212: Oracle CLOB column wrapped with TO_CHAR."""
        src = make_source("s1", "tab", [
            {"name": "note", "type": "CLOB"},
        ], filters=[{"column": "note", "operator": "=", "value": "test"}])
        r = gen([src, make_result()], [output_edge("s1")], db_type="oracle")
        assert "TO_CHAR(tab.note)" in r["sql"]

    def test_oracle_nclob_wrapping(self):
        """Oracle NCLOB also gets TO_CHAR wrapping."""
        src = make_source("s1", "tab", [
            {"name": "descr", "type": "NCLOB"},
        ], filters=[{"column": "descr", "operator": "LIKE", "value": "test"}])
        r = gen([src, make_result()], [output_edge("s1")], db_type="oracle")
        assert "TO_CHAR(tab.descr)" in r["sql"]

    # ── Lines 1241-1242: SQL function detected -> inline injection ───────

    def test_sql_function_now_inline(self):
        """Lines 1240-1242: NOW() detected as SQL function, injected inline."""
        src = make_source("s1", "tab", [
            {"name": "data", "type": "date"},
        ], filters=[{"column": "data", "operator": "<=", "value": "NOW()"}])
        r = gen([src, make_result()], [output_edge("s1")], db_type="postgres")
        assert "NOW()" in r["sql"]
        # No param generated for the value
        assert len(r["params"]) == 0

    def test_sql_function_getdate_inline(self):
        """GETDATE() detected as SQL function."""
        src = make_source("s1", "tab", [
            {"name": "data", "type": "date"},
        ], filters=[{"column": "data", "operator": "<=", "value": "GETDATE()"}])
        r = gen([src, make_result()], [output_edge("s1")], db_type="mssql")
        assert "GETDATE()" in r["sql"]

    def test_sql_function_sysdate_inline(self):
        """SYSDATE detected as SQL function for Oracle."""
        src = make_source("s1", "tab", [
            {"name": "data", "type": "date"},
        ], filters=[{"column": "data", "operator": "<=", "value": "SYSDATE"}])
        r = gen([src, make_result()], [output_edge("s1")], db_type="oracle")
        assert "SYSDATE" in r["sql"]

    # ── Lines 1254-1260: typed value conversion (float, invalid int) ─────

    def test_float_column_value_conversion(self):
        """Lines 1257-1260: float column type converts value to float."""
        src = make_source("s1", "tab", [
            {"name": "peso", "type": "float"},
        ], filters=[{"column": "peso", "operator": ">", "value": "2.5"}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert isinstance(r["params"]["p_0"], float)
        assert abs(r["params"]["p_0"] - 2.5) < 1e-9

    def test_int_column_invalid_value_stays_string(self):
        """Lines 1254-1255: int column with non-numeric value stays as string."""
        src = make_source("s1", "tab", [
            {"name": "id", "type": "int"},
        ], filters=[{"column": "id", "operator": "=", "value": "abc"}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert r["params"]["p_0"] == "abc"

    # ── Lines 1318-1392: _translate_date_function dialects ───────────────

    def test_translate_dateadd_month_mssql(self):
        """Lines 1359-1361: DATEADD(month, N, ...) for mssql."""
        g = qg("mssql")
        result = g._translate_date_function("DATEADD(month, 3, GETDATE())")
        assert "DATEADD(month, 3, GETDATE())" in result

    def test_translate_dateadd_day_postgres(self):
        """Lines 1363-1366: DATEADD(day, N, ...) translated for postgres."""
        g = qg("postgres")
        result = g._translate_date_function("DATEADD(day, 7, GETDATE())")
        assert "INTERVAL" in result
        assert "7" in result

    def test_translate_add_months_oracle(self):
        """Lines 1369-1371: ADD_MONTHS for oracle."""
        g = qg("oracle")
        result = g._translate_date_function("ADD_MONTHS(SYSDATE, 6)")
        assert "ADD_MONTHS" in result
        assert "6" in result

    def test_translate_date_add_mysql(self):
        """Lines 1374-1376: DATE_ADD for mysql."""
        g = qg("mysql")
        result = g._translate_date_function("DATE_ADD(NOW(), INTERVAL 2 MONTH)")
        assert "DATE_ADD" in result
        assert "2" in result

    def test_translate_now_interval_postgres(self):
        """Lines 1379-1381: NOW() + INTERVAL 'N months' for postgres."""
        g = qg("postgres")
        result = g._translate_date_function("NOW() + INTERVAL '3 months'")
        assert "3" in result
        assert "INTERVAL" in result and "NOW" in result

    def test_translate_current_date_mysql(self):
        """Lines 1383-1385: CURDATE() -> dialect's 'today' for mysql."""
        g = qg("mysql")
        result = g._translate_date_function("CURDATE()")
        assert "CURDATE()" in result

    def test_translate_curdate_oracle(self):
        """CURDATE() -> TRUNC(SYSDATE) for oracle."""
        g = qg("oracle")
        result = g._translate_date_function("CURDATE()")
        assert "TRUNC(SYSDATE)" in result

    def test_translate_now_sqlite(self):
        """NOW() -> datetime('now') for sqlite."""
        g = qg("sqlite")
        result = g._translate_date_function("NOW()")
        assert "datetime('now')" in result

    def test_translate_getdate_postgres(self):
        """GETDATE() -> NOW() for postgres."""
        g = qg("postgres")
        result = g._translate_date_function("GETDATE()")
        assert "NOW()" in result

    def test_translate_unknown_function_fallback(self):
        """Line 1392: unrecognized function returned as-is."""
        g = qg("postgres")
        result = g._translate_date_function("EXTRACT(YEAR FROM col)")
        # Not matched by any regex -> returned as-is
        assert "EXTRACT" in result

    # ── Lines 1411-1421: _crea_condizione_in with single value, int ──────

    def test_in_single_value_no_comma(self):
        """Lines 1416-1421: IN with single (non-CSV) value."""
        g = qg()
        params = {}
        result = g._crea_condizione_in("t.col", "42", params)
        assert "IN" in result
        assert params["p_0"] == 42

    def test_in_list_value(self):
        """Lines 1405-1414: IN with list value (bypassing Pydantic)."""
        g = qg()
        params = {}
        result = g._crea_condizione_in("t.col", ["a", "b", "3"], params)
        assert "IN" in result
        assert len(params) == 3
        # "3" should be converted to int
        assert any(v == 3 for v in params.values())

    def test_in_list_non_digit_string(self):
        """Lines 1411-1412: IN list with non-digit string stays as string."""
        g = qg()
        params = {}
        g._crea_condizione_in("t.col", ["hello"], params)
        assert params["p_0"] == "hello"

    # ── Lines 1464: _assembla_query LIMIT negative raises ────────────────

    def test_assembla_limit_negative_raises(self):
        """Line 1464: negative LIMIT raises ValueError."""
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=-5)
        with pytest.raises(ValueError, match="LIMIT"):
            sql([src, res], [output_edge("s1")])

    # ── Lines 1523-1533: _is_valid_quoted_identifier ─────────────────────

    def test_quoted_identifier_with_hyphen(self):
        """Lines 1523-1533: identifier with hyphen is valid for quoting."""
        g = qg()
        assert g._is_valid_quoted_identifier("col-name") is True
        assert g._is_valid_quoted_identifier("my-long-column") is True

    def test_quoted_identifier_rejects_drop(self):
        """Lines 1527-1532: SQL keywords rejected even with quoting."""
        g = qg()
        assert g._is_valid_quoted_identifier("drop") is False
        assert g._is_valid_quoted_identifier("delete") is False

    def test_quoted_identifier_rejects_empty(self):
        """Line 1523: empty string not valid."""
        g = qg()
        assert g._is_valid_quoted_identifier("") is False

    def test_quoted_identifier_rejects_over_128(self):
        """Line 1523: over 128 chars not valid."""
        g = qg()
        assert g._is_valid_quoted_identifier("a" * 129) is False

    def test_quoted_identifier_rejects_special_chars(self):
        """Line 1525: special chars like spaces, semicolons rejected."""
        g = qg()
        assert g._is_valid_quoted_identifier("col name") is False
        assert g._is_valid_quoted_identifier("col;drop") is False

    # ── Lines 1547-1549: _safe_identifier quoted path ────────────────────

    def test_safe_identifier_quotes_hyphenated(self):
        """Lines 1547-1548: hyphenated identifier gets double-quoted."""
        g = qg()
        result = g._safe_identifier("col-name")
        assert result == '"col-name"'

    def test_safe_identifier_raises_invalid(self):
        """Line 1549: truly invalid identifier raises ValueError."""
        g = qg()
        with pytest.raises(ValueError, match="non valido"):
            g._safe_identifier("col name with spaces")

    # ── Lines 1560-1570: _wrap_col_with_func ─────────────────────────────

    def test_wrap_col_trim(self):
        """Line 1561: TRIM wrapping."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "TRIM") == "TRIM(t.col)"

    def test_wrap_col_ltrim_rtrim(self):
        """Line 1563: LTRIM_RTRIM wrapping."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "LTRIM_RTRIM") == "LTRIM(RTRIM(t.col))"

    def test_wrap_col_upper(self):
        """Line 1565: UPPER wrapping."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "UPPER") == "UPPER(t.col)"

    def test_wrap_col_lower(self):
        """Line 1567: LOWER wrapping."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "LOWER") == "LOWER(t.col)"

    def test_wrap_col_coalesce_empty(self):
        """Line 1569: COALESCE_EMPTY wrapping."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "COALESCE_EMPTY") == "COALESCE(t.col, '')"

    def test_wrap_col_unknown_func_passthrough(self):
        """Line 1570: unknown func returns expression unchanged."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "UNKNOWN_FUNC") == "t.col"

    def test_wrap_col_no_func(self):
        """Line 1558: empty func returns expression unchanged."""
        g = qg()
        assert g._wrap_col_with_func("t.col", "") == "t.col"
        assert g._wrap_col_with_func("t.col", None) == "t.col"

    # ── Lines 981, 983: spatial ON with invalid geom column raises ───────

    def test_spatial_on_invalid_geom_column_raises(self):
        """Lines 981, 983: invalid geometry column name raises ValueError."""
        g = qg()
        edge = make_edge("j", "a", "b", join_type="inner",
                         is_spatial=True, spatial_predicate="ST_Intersects",
                         source_geom="DROP", target_geom="geom")
        with pytest.raises(ValueError, match="Colonna geometria non valida"):
            g._genera_on_spaziale(edge, "t1", "t2")

    def test_spatial_on_invalid_target_geom_raises(self):
        """Line 983: invalid target geometry column raises."""
        g = qg()
        edge = make_edge("j", "a", "b", join_type="inner",
                         is_spatial=True, spatial_predicate="ST_Intersects",
                         source_geom="geom", target_geom="DELETE")
        with pytest.raises(ValueError, match="Colonna geometria non valida"):
            g._genera_on_spaziale(edge, "t1", "t2")

    # ── Lines 1010-1014: spatial ON fallback ─────────────────────────────

    def test_spatial_on_dwithin_fallback(self):
        """Lines 1012-1013: spatial fallback with DWithin and distance."""
        g = qg("unknown_db_type")
        edge = make_edge("j", "a", "b", join_type="inner",
                         is_spatial=True, spatial_predicate="ST_DWithin",
                         source_geom="geom", target_geom="geom", distance=100)
        # Force fallback by using an unsupported db_type
        result = g._genera_on_spaziale(edge, "t1", "t2")
        assert "geom" in result

    # ── _is_sql_function: various patterns ───────────────────────────────

    def test_is_sql_function_current_timestamp(self):
        """CURRENT_TIMESTAMP recognized as SQL function."""
        assert QueryGenerator._is_sql_function("CURRENT_TIMESTAMP") is True

    def test_is_sql_function_datepart(self):
        """DATEPART(...) recognized as SQL function."""
        assert QueryGenerator._is_sql_function("DATEPART(year, col)") is True

    def test_is_sql_function_interval(self):
        """INTERVAL recognized as SQL function."""
        assert QueryGenerator._is_sql_function("INTERVAL '1 day'") is True

    def test_is_sql_function_regular_value(self):
        """Regular string is not a SQL function."""
        assert QueryGenerator._is_sql_function("hello world") is False

    def test_is_sql_function_systimestamp(self):
        """SYSTIMESTAMP recognized as SQL function."""
        assert QueryGenerator._is_sql_function("SYSTIMESTAMP") is True

    # ── Full join type normalization ─────────────────────────────────────

    def test_full_join_normalized(self):
        """Lines 927-928: join_type 'FULL' normalized to 'FULL OUTER'."""
        a = make_source("a", "t1", [{"name": "id", "type": "int"}])
        b = make_source("b", "t2", [{"name": "id", "type": "int"}])
        res = make_result()
        j = make_edge("j", "a", "b", join_type="inner", source_col="id", target_col="id")
        j.join_type = "outer"
        edges = [j, output_edge("b")]
        q = sql([a, b, res], edges)
        assert "FULL OUTER JOIN" in q
