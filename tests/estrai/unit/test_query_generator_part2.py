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
# Schema qualification
# =============================================================================

class TestSchemaQualification:

    def test_mssql_default_dbo(self):
        src = make_source("s1", "prodotti", [{"name": "id", "type": "int"}])
        q = sql([src, make_result()], [output_edge("s1")], db_type="mssql")
        assert "dbo.prodotti" in q

    def test_schema_custom_passato(self):
        src = make_source("s1", "prodotti", [{"name": "id", "type": "int"}])
        q = sql([src, make_result()], [output_edge("s1")], db_type="postgres", db_schema="myschema")
        assert "myschema.prodotti" in q

    def test_mysql_no_schema(self):
        src = make_source("s1", "prodotti", [{"name": "id", "type": "int"}])
        q = sql([src, make_result()], [output_edge("s1")], db_type="mysql")
        assert "dbo." not in q
        assert "public." not in q

    def test_tabella_con_schema_gia_presente_non_doppio(self):
        src = make_source("s1", "myschema.prodotti", [{"name": "id", "type": "int"}])
        q = sql([src, make_result()], [output_edge("s1")], db_type="postgres", db_schema="public")
        assert "public.myschema.prodotti" not in q


# =============================================================================
# Findings backend documentati
# =============================================================================

class TestBackendFindings:

    def test_finding_filter_value_solo_stringa(self):
        """
        FINDING B-BE-ESTRAI-02: Filter.value accetta solo str (Pydantic).
        Il branch isinstance(value, list) in _crea_condizione_in() è
        irraggiungibile via API — codice morto.
        """
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            Filter(column="id", operator="IN", value=[1, 2, 3])  # type: ignore


# =============================================================================
# Errori e edge case
# =============================================================================

class TestErrori:

    def test_nessuna_colonna_selezionata_raise(self):
        src = make_source("s1", "tab", [
            {"name": "id",   "type": "int", "visible": False},
            {"name": "nome", "type": "str", "visible": False},
        ])
        with pytest.raises((ValueError, Exception)):
            sql([src, make_result()], [output_edge("s1")])

    def test_result_node_mancante_raise(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        with pytest.raises((ValueError, Exception)):
            qg().generate_sql([src], [], "result_inesistente")

    def test_params_reset_tra_generate(self):
        """I parametri si resettano ad ogni generate_sql."""
        src = make_source("t", "tab", [{"name": "col", "type": "str"}])
        src.filters = [Filter(column="col", operator="=", value="x")]
        res = make_result()
        g = qg()
        r1 = g.generate_sql([src, res], [output_edge("t")], res.id)
        r2 = g.generate_sql([src, res], [output_edge("t")], res.id)
        assert "p_0" in r1["params"]
        assert "p_0" in r2["params"]

    def test_tabelle_usate_nel_risultato(self):
        src = make_source("s1", "mia_tabella", [{"name": "id", "type": "int"}])
        r = gen([src, make_result()], [output_edge("s1")])
        assert "mia_tabella" in r["tables_used"]

    def test_columns_count_corretto(self):
        """columns_count conta solo le colonne visibili."""
        src = make_source("s1", "tab", [
            {"name": "id",      "type": "int", "visible": True},
            {"name": "nome",    "type": "str", "visible": True},
            {"name": "segreto", "type": "str", "visible": False},
        ])
        r = gen([src, make_result()], [output_edge("s1")])
        assert r["columns_count"] == 2


# =============================================================================
# ORDER BY
# =============================================================================

class TestOrderBy:

    def test_order_by_singola_colonna_asc(self):
        """ORDER BY con una colonna ASC."""
        src = make_source("s1", "clienti", [
            {"name": "id", "type": "int"},
            {"name": "nome", "type": "str"},
        ])
        res = make_result(order_by_enabled=True, order_by_columns=[
            {"column": "nome", "direction": "ASC"}
        ])
        q = sql([src, res], [output_edge("s1")])
        assert "ORDER BY" in q
        assert "nome" in q.split("ORDER BY")[1]
        assert "ASC" in q.split("ORDER BY")[1]

    def test_order_by_singola_colonna_desc(self):
        """ORDER BY con una colonna DESC."""
        src = make_source("s1", "clienti", [
            {"name": "id", "type": "int"},
            {"name": "nome", "type": "str"},
        ])
        res = make_result(order_by_enabled=True, order_by_columns=[
            {"column": "id", "direction": "DESC"}
        ])
        q = sql([src, res], [output_edge("s1")])
        assert "ORDER BY" in q
        assert "DESC" in q.split("ORDER BY")[1]

    def test_order_by_multiple_colonne(self):
        """ORDER BY con più colonne."""
        src = make_source("s1", "clienti", [
            {"name": "nome", "type": "str"},
            {"name": "eta", "type": "int"},
        ])
        res = make_result(order_by_enabled=True, order_by_columns=[
            {"column": "nome", "direction": "ASC"},
            {"column": "eta", "direction": "DESC"}
        ])
        q = sql([src, res], [output_edge("s1")])
        order_part = q.split("ORDER BY")[1]
        assert "nome" in order_part
        assert "eta" in order_part
        assert "ASC" in order_part
        assert "DESC" in order_part

    def test_order_by_disabilitato(self):
        """ORDER BY non presente se disabilitato."""
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(order_by_enabled=False)
        q = sql([src, res], [output_edge("s1")])
        assert "ORDER BY" not in q

    def test_order_by_prima_di_limit(self):
        """ORDER BY deve apparire prima di LIMIT."""
        src = make_source("s1", "tab", [
            {"name": "id", "type": "int"},
            {"name": "nome", "type": "str"},
        ])
        res = make_result(limit=10, order_by_enabled=True, order_by_columns=[
            {"column": "nome", "direction": "ASC"}
        ])
        q = sql([src, res], [output_edge("s1")])
        assert q.index("ORDER BY") < q.index("LIMIT")

    def test_order_by_dopo_group_by(self):
        """ORDER BY deve apparire dopo GROUP BY."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[{"column": "importo", "function": "SUM", "alias": "totale"}],
            order_by_enabled=True,
            order_by_columns=[{"column": "cliente", "direction": "ASC"}]
        )
        q = sql([src, res], [output_edge("s1")])
        assert "GROUP BY" in q
        assert "ORDER BY" in q
        assert q.index("GROUP BY") < q.index("ORDER BY")

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_order_by_tutti_db(self, db_type):
        """ORDER BY funziona su tutti i database."""
        src = make_source("s1", "tab", [{"name": "nome", "type": "str"}])
        res = make_result(order_by_enabled=True, order_by_columns=[
            {"column": "nome", "direction": "DESC"}
        ])
        q = sql([src, res], [output_edge("s1")], db_type=db_type)
        assert "ORDER BY" in q
        assert "DESC" in q


# =============================================================================
# HAVING
# =============================================================================

class TestHaving:

    def test_having_con_count(self):
        """HAVING COUNT(*) > N."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[{"column": "*", "function": "COUNT", "alias": "conteggio"}],
            having_enabled=True,
            having_conditions=[{"function": "COUNT", "column": "*", "operator": ">", "value": "5"}]
        )
        r = gen([src, res], [output_edge("s1")])
        q = r["sql"]
        assert "HAVING" in q
        assert "COUNT(*)" in q
        # Il valore è parametrizzato
        assert 5 in r["params"].values() or "5" in [str(v) for v in r["params"].values()]

    def test_having_con_sum(self):
        """HAVING SUM(col) >= N."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[{"column": "importo", "function": "SUM", "alias": "totale"}],
            having_enabled=True,
            having_conditions=[{"function": "SUM", "column": "importo", "operator": ">=", "value": "1000"}]
        )
        r = gen([src, res], [output_edge("s1")])
        q = r["sql"]
        assert "HAVING" in q
        assert "SUM(importo)" in q

    def test_having_dopo_group_by(self):
        """HAVING deve apparire dopo GROUP BY."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[{"column": "*", "function": "COUNT", "alias": "n"}],
            having_enabled=True,
            having_conditions=[{"function": "COUNT", "column": "*", "operator": ">", "value": "1"}]
        )
        q = sql([src, res], [output_edge("s1")])
        assert q.index("GROUP BY") < q.index("HAVING")

    def test_having_senza_group_by_ignorato(self):
        """HAVING senza GROUP BY non genera clausola."""
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(
            having_enabled=True,
            having_conditions=[{"function": "COUNT", "column": "*", "operator": ">", "value": "1"}]
        )
        q = sql([src, res], [output_edge("s1")])
        assert "HAVING" not in q

    def test_having_multiple_conditions(self):
        """HAVING con più condizioni unite da AND."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[
                {"column": "*", "function": "COUNT", "alias": "n"},
                {"column": "importo", "function": "SUM", "alias": "totale"}
            ],
            having_enabled=True,
            having_conditions=[
                {"function": "COUNT", "column": "*", "operator": ">", "value": "2"},
                {"function": "SUM", "column": "importo", "operator": ">=", "value": "500"}
            ]
        )
        q = sql([src, res], [output_edge("s1")])
        assert "HAVING" in q
        assert "AND" in q.split("HAVING")[1]

    def test_having_prima_di_order_by(self):
        """HAVING deve apparire prima di ORDER BY."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[{"column": "*", "function": "COUNT", "alias": "n"}],
            having_enabled=True,
            having_conditions=[{"function": "COUNT", "column": "*", "operator": ">", "value": "1"}],
            order_by_enabled=True,
            order_by_columns=[{"column": "cliente", "direction": "ASC"}]
        )
        q = sql([src, res], [output_edge("s1")])
        assert q.index("HAVING") < q.index("ORDER BY")

    @pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle", "sqlite"])
    def test_having_tutti_db(self, db_type):
        """HAVING funziona su tutti i database."""
        src = make_source("s1", "ordini", [
            {"name": "cliente", "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["cliente"],
            aggregations=[{"column": "*", "function": "COUNT", "alias": "n"}],
            having_enabled=True,
            having_conditions=[{"function": "COUNT", "column": "*", "operator": ">", "value": "3"}]
        )
        q = sql([src, res], [output_edge("s1")], db_type=db_type)
        assert "HAVING" in q
        assert "COUNT(*)" in q
