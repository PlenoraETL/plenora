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
# COVERAGE — test mirati per linee scoperte
# =============================================================================

class TestCoverageTargeted:
    """Test mirati a linee specifiche non coperte dai test esistenti."""

    # --- Line 130: ValueError when no tables connected to result ---

    def test_no_tables_connected_raises_valueerror(self):
        """Line 130: result senza tabelle connesse alza ValueError."""
        res = make_result()
        with pytest.raises(ValueError, match="Nessuna tabella connessa"):
            # result node exists but no edges connect any source to it
            gen([res], [], db_type="postgres")

    # --- Lines 182-184: _get_node_table_name for datasource_node ---

    def test_datasource_node_table_name(self):
        """Lines 182-184: datasource_node sanitizza nome con _urby_ds_ prefix."""
        g = qg()
        node = Node(
            id="ds1", type="datasource_node",
            position=Position(x=0, y=0),
            datasource_name="my test.src",
        )
        name = g._get_node_table_name(node)
        assert name == "_urby_ds_my_test_src"
        assert " " not in name
        assert "." not in name
        assert "-" not in name

    def test_datasource_node_fallback_to_id(self):
        """Lines 182-184: datasource_node senza datasource_name usa node.id."""
        g = qg()
        node = Node(
            id="ds_fallback", type="datasource_node",
            position=Position(x=0, y=0),
        )
        name = g._get_node_table_name(node)
        assert name == "_urby_ds_ds_fallback"

    # --- Line 195: _qualify_table_name with already-qualified name ---

    def test_qualify_table_name_already_qualified(self):
        """Line 195/198: table name with dot is not double-qualified."""
        g = qg()
        g.db_schema = "public"
        result = g._qualify_table_name("myschema.mytable")
        assert result == "myschema.mytable"
        assert "public.myschema" not in result

    def test_qualify_table_name_empty(self):
        """Line 194: empty table name returned as-is."""
        g = qg()
        g.db_schema = "dbo"
        assert g._qualify_table_name("") == ""

    # --- Lines 242-246: GROUP BY config from result_node.data dict ---

    def test_group_by_from_data_dict(self):
        """Lines 242-246: GROUP BY read from result_node.data when attributes are off."""
        src = make_source("s1", "vendite", [
            {"name": "comune", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        # Build result node with group_by_enabled=False on the attribute,
        # but group_by_enabled=True inside .data dict
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["comune"],
            "aggregations": [{"column": "importo", "function": "SUM", "alias": "tot"}],
        }
        q = sql([src, res], [output_edge("s1")])
        assert "GROUP BY" in q
        assert "SUM(" in q
        assert "AS tot" in q

    # --- Lines 501-522: Calculated columns — manual SQL and function+params ---

    def test_calculated_column_manual_sql(self):
        """Lines 498-500: is_manual=True uses raw SQL expression."""
        src = make_source("s1", "tab", [
            {"name": "prezzo", "type": "real"},
        ], calculated_columns=[{
            "name": "sconto",
            "is_manual": True,
            "sql": "s1.prezzo * 0.10 AS sconto",
        }])
        q = sql([src, make_result()], [output_edge("s1")])
        assert "0.10" in q
        assert "sconto" in q

    def test_calculated_column_function_params(self):
        """Lines 501-511: function_name + params format uses translate_calculated_column."""
        src = make_source("s1", "geo", [
            {"name": "geom", "type": "geometry"},
        ], calculated_columns=[{
            "name": "area_calc",
            "function_name": "AREA",
            "params": {"column": "geom"},
        }])
        q = sql([src, make_result()], [output_edge("s1")])
        assert "area_calc" in q.lower() and "AREA" in q.upper()

    # --- Lines 526-527: Failed calculated column translation ---

    def test_calculated_column_invalid_function_raises(self):
        """Lines 526-528: invalid function raises ValueError."""
        src = make_source("s1", "tab", [
            {"name": "col", "type": "str"},
        ], calculated_columns=[{
            "name": "bad_calc",
            "function_name": "NONEXISTENT_FUNC_XYZ",
            "params": {"column": "col"},
        }])
        with pytest.raises(ValueError, match="Errore colonna calcolata"):
            sql([src, make_result()], [output_edge("s1")])

    # --- Lines 589-594, 600: GROUP BY aggregation edge cases ---

    def test_aggregation_first_function(self):
        """Lines 580, 604: FIRST is in valid_agg_functions and generates FIRST(col)."""
        src = make_source("s1", "tab", [
            {"name": "gruppo", "type": "str"},
            {"name": "valore", "type": "str"},
        ])
        # Bypass Pydantic validation via .data dict (lines 242-246)
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["gruppo"],
            "aggregations": [{"column": "valore", "function": "FIRST", "alias": "primo"}],
        }
        q = sql([src, res], [output_edge("s1")])
        assert "FIRST(" in q
        assert "AS primo" in q

    def test_aggregation_last_function(self):
        """Lines 580, 604: LAST is in valid_agg_functions and generates LAST(col)."""
        src = make_source("s1", "tab", [
            {"name": "gruppo", "type": "str"},
            {"name": "valore", "type": "str"},
        ])
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["gruppo"],
            "aggregations": [{"column": "valore", "function": "LAST", "alias": "ultimo"}],
        }
        q = sql([src, res], [output_edge("s1")])
        assert "LAST(" in q
        assert "AS ultimo" in q

    def test_count_star_only_valid_for_count(self):
        """Line 600: SUM(*) raises ValueError — only COUNT can use *."""
        src = make_source("s1", "tab", [
            {"name": "gruppo", "type": "str"},
        ])
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["gruppo"],
            "aggregations": [{"column": "*", "function": "SUM", "alias": "oops"}],
        }
        with pytest.raises(ValueError, match="Solo COUNT"):
            sql([src, res], [output_edge("s1")])

    def test_invalid_aggregation_function_raises(self):
        """Line 594: invalid agg function raises ValueError."""
        src = make_source("s1", "tab", [
            {"name": "gruppo", "type": "str"},
            {"name": "val", "type": "int"},
        ])
        res = make_result()
        res.group_by_enabled = False
        res.data = {
            "group_by_enabled": True,
            "group_by_columns": ["gruppo"],
            "aggregations": [{"column": "val", "function": "MEDIAN", "alias": "med"}],
        }
        with pytest.raises(ValueError, match="non valida"):
            sql([src, res], [output_edge("s1")])

    # --- LIMIT per DB: oracle FETCH FIRST, mssql TOP ---

    @pytest.mark.parametrize("db_type,expected,not_expected", [
        ("oracle", "FETCH FIRST 10 ROWS ONLY", "LIMIT"),
        ("mssql", "TOP 10", "LIMIT"),
    ])
    def test_limit_db_specific(self, db_type, expected, not_expected):
        """LIMIT syntax varies per database."""
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=10)
        q = sql([src, res], [output_edge("s1")], db_type=db_type)
        assert expected in q
        assert not_expected not in q

    # --- Spatial JOIN: intersects ---

    def test_spatial_join_intersects(self):
        """Spatial join with ST_Intersects predicate."""
        src_a = make_source("a", "punti", [
            {"name": "id", "type": "int"},
            {"name": "geom", "type": "geometry"},
        ])
        src_b = make_source("b", "zone", [
            {"name": "id", "type": "int", "visible": False},
            {"name": "geom", "type": "geometry", "visible": False},
            {"name": "nome", "type": "str"},
        ])
        j = make_edge("j", "a", "b", join_type="inner",
                      is_spatial=True, spatial_predicate="ST_Intersects",
                      source_geom="geom", target_geom="geom")
        q = sql([src_a, src_b, make_result()], [j, output_edge("b")])
        lower = q.lower()
        assert "intersects" in lower
        assert "join" in lower

    # --- Buffer JOIN: spatial join with distance ---

    def test_spatial_join_with_buffer_distance(self):
        """Spatial join with ST_DWithin and distance parameter."""
        src_a = make_source("a", "stazioni", [
            {"name": "id", "type": "int"},
            {"name": "geom", "type": "geometry"},
        ])
        src_b = make_source("b", "fermate", [
            {"name": "geom", "type": "geometry", "visible": False},
            {"name": "nome", "type": "str"},
        ])
        j = make_edge("j", "a", "b", join_type="inner",
                      is_spatial=True, spatial_predicate="ST_DWithin",
                      source_geom="geom", target_geom="geom", distance=1000.0)
        q = sql([src_a, src_b, make_result()], [j, output_edge("b")])
        lower = q.lower()
        assert "dwithin" in lower
        assert "1000" in q

    # --- Filter operators: LIKE, IN, IS NULL, IS NOT NULL, BETWEEN ---

    def test_filter_like_operator(self):
        """LIKE wraps value with %."""
        src = make_source("s1", "tab", [{"name": "nome", "type": "str"}],
                          filters=[{"column": "nome", "operator": "LIKE", "value": "Mar"}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert "LIKE" in r["sql"]
        assert r["params"]["p_0"] == "%Mar%"

    def test_filter_in_operator(self):
        """IN splits CSV string into multiple params."""
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}],
                          filters=[{"column": "id", "operator": "IN", "value": "1,2,3"}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert "IN" in r["sql"]
        assert len(r["params"]) == 3
        assert set(r["params"].values()) == {1, 2, 3}

    def test_filter_is_null(self):
        """IS NULL produces no params."""
        src = make_source("s1", "tab", [{"name": "col", "type": "str"}],
                          filters=[{"column": "col", "operator": "IS NULL", "value": ""}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert "IS NULL" in r["sql"]
        assert len(r["params"]) == 0

    def test_filter_is_not_null(self):
        """IS NOT NULL produces no params."""
        src = make_source("s1", "tab", [{"name": "col", "type": "str"}],
                          filters=[{"column": "col", "operator": "IS NOT NULL", "value": ""}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert "IS NOT NULL" in r["sql"]
        assert len(r["params"]) == 0

    def test_filter_between_not_supported(self):
        """BETWEEN is not in valid operators — raises ValueError."""
        src = make_source("s1", "tab", [{"name": "val", "type": "int"}],
                          filters=[{"column": "val", "operator": "BETWEEN", "value": "1,10"}])
        with pytest.raises(ValueError, match="Operatore non valido"):
            sql([src, make_result()], [output_edge("s1")])

    # --- Schema qualification: db_schema='dbo' with mssql ---

    def test_schema_qualification_mssql_dbo(self):
        """Schema dbo is applied to mssql tables."""
        src = make_source("s1", "clienti", [{"name": "id", "type": "int"}])
        q = sql([src, make_result()], [output_edge("s1")], db_type="mssql", db_schema="dbo")
        assert "dbo.clienti" in q

    def test_schema_qualification_custom(self):
        """Custom schema applied to postgres."""
        src = make_source("s1", "ordini", [{"name": "id", "type": "int"}])
        q = sql([src, make_result()], [output_edge("s1")], db_type="postgres", db_schema="vendite")
        assert "vendite.ordini" in q

    # --- DISTINCT + GROUP BY: DISTINCT suppressed ---

    def test_distinct_suppressed_with_group_by(self):
        """DISTINCT is suppressed when GROUP BY is active."""
        src = make_source("s1", "tab", [
            {"name": "cat", "type": "str"},
            {"name": "val", "type": "int"},
        ])
        res = make_result(
            distinct=True,
            group_by_enabled=True,
            group_by_columns=["cat"],
            aggregations=[{"column": "val", "function": "SUM", "alias": "totale"}],
        )
        q = sql([src, res], [output_edge("s1")])
        assert "DISTINCT" not in q
        assert "GROUP BY" in q
        assert "SUM(" in q

    # --- Geometry column wrapping: ST_AsText in SELECT ---

    def test_geometry_column_wrapped_st_astext_postgres(self):
        """Geometry column wrapped with ST_AsText for postgres."""
        src = make_source("s1", "luoghi", [
            {"name": "id", "type": "int"},
            {"name": "geom", "type": "geometry:polygon:4326"},
        ])
        q = sql([src, make_result()], [output_edge("s1")], db_type="postgres")
        lower = q.lower()
        assert "st_astext" in lower
        assert "geom" in lower

    def test_geometry_column_wrapped_mssql(self):
        """Geometry column wrapped for mssql."""
        src = make_source("s1", "luoghi", [
            {"name": "id", "type": "int"},
            {"name": "geom", "type": "geometry:point:4326"},
        ])
        q = sql([src, make_result()], [output_edge("s1")], db_type="mssql")
        lower = q.lower()
        # mssql may use .STAsText() or similar
        assert "astext" in lower
