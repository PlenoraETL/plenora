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
# SELECT BASE
# =============================================================================

class TestSelect:

    def test_select_tutte_colonne_visibili(self):
        """SELECT usa table_name (non node_id) quando non c'è alias."""
        src = make_source("s1", "clienti", [
            {"name": "id",   "type": "int"},
            {"name": "nome", "type": "str"},
            {"name": "eta",  "type": "int"},
        ])
        res = make_result()
        q = sql([src, res], [output_edge("s1")])
        assert "clienti.id" in q
        assert "clienti.nome" in q
        assert "clienti.eta" in q

    def test_colonne_nascoste_escluse(self):
        src = make_source("s1", "ordini", [
            {"name": "id",      "type": "int",  "visible": False},
            {"name": "importo", "type": "real", "visible": True},
        ])
        res = make_result()
        q = sql([src, res], [output_edge("s1")])
        assert "importo" in q
        assert "ordini.id" not in q

    def test_alias_colonna(self):
        src = make_source("s1", "tabella", [
            {"name": "nome_cliente", "type": "str", "alias": "cliente"},
        ])
        res = make_result()
        q = sql([src, res], [output_edge("s1")])
        assert "AS cliente" in q
        assert "nome_cliente" in q

    def test_alias_tabella(self):
        src = make_source("s1", "tabella_lunga", [
            {"name": "col1", "type": "str"},
        ], alias="t")
        res = make_result()
        q = sql([src, res], [output_edge("s1")])
        assert "tabella_lunga AS t" in q
        assert "t.col1" in q

    def test_colonna_geometry_wrappata_st_astext(self):
        src = make_source("s1", "luoghi", [
            {"name": "id",   "type": "int"},
            {"name": "geom", "type": "geometry:point:4326"},
        ])
        res = make_result()
        q = sql([src, res], [output_edge("s1")])
        lower = q.lower()
        assert "st_astext" in lower

    def test_geometry_oracle_usa_sdo(self):
        src = make_source("s1", "luoghi", [
            {"name": "id",   "type": "int"},
            {"name": "geom", "type": "geometry"},
        ])
        res = make_result()
        q = sql([src, res], [output_edge("s1")], db_type="oracle")
        lower = q.lower()
        assert "sdo_util" in lower or "to_wktgeometry" in lower


# =============================================================================
# DISTINCT
# =============================================================================

class TestDistinct:

    def test_distinct_nella_select(self):
        src = make_source("s1", "tab", [{"name": "nome", "type": "str"}])
        res = make_result(distinct=True)
        q = sql([src, res], [output_edge("s1")])
        assert "DISTINCT" in q

    def test_no_distinct_di_default(self):
        src = make_source("s1", "tab", [{"name": "nome", "type": "str"}])
        res = make_result(distinct=False)
        q = sql([src, res], [output_edge("s1")])
        assert "DISTINCT" not in q


# =============================================================================
# LIMIT — sintassi per database
# =============================================================================

class TestLimit:

    def test_limit_postgres(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=50)
        q = sql([src, res], [output_edge("s1")], db_type="postgres")
        assert q.strip().endswith("LIMIT 50")

    def test_limit_mysql(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=10)
        q = sql([src, res], [output_edge("s1")], db_type="mysql")
        assert q.strip().endswith("LIMIT 10")

    def test_limit_sqlite(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=5)
        q = sql([src, res], [output_edge("s1")], db_type="sqlite")
        assert q.strip().endswith("LIMIT 5")

    def test_limit_oracle(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=100)
        q = sql([src, res], [output_edge("s1")], db_type="oracle")
        assert "FETCH FIRST 100 ROWS ONLY" in q
        assert "LIMIT" not in q

    def test_limit_mssql(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=25)
        q = sql([src, res], [output_edge("s1")], db_type="mssql")
        assert "TOP 25" in q
        assert "LIMIT" not in q

    def test_nessun_limit_se_none(self):
        src = make_source("s1", "tab", [{"name": "id", "type": "int"}])
        res = make_result(limit=None)
        q = sql([src, res], [output_edge("s1")])
        assert "LIMIT" not in q
        assert "TOP" not in q
        assert "FETCH" not in q


# =============================================================================
# WHERE — operatori
# =============================================================================

class TestWhere:

    def _src(self, op, value, col_type="str"):
        """Costruisce nodo con filtro, bypassando Pydantic per casi edge."""
        src = make_source("t", "tab", [{"name": "col", "type": col_type}])
        src.filters = [Filter(column="col", operator=op, value=str(value) if not isinstance(value, str) else value)]
        return src

    def test_eq(self):
        r = gen([self._src("=", "Roma"), make_result()], [output_edge("t")])
        assert "= :p_0" in r["sql"]
        assert r["params"]["p_0"] == "Roma"

    def test_neq(self):
        r = gen([self._src("!=", "Roma"), make_result()], [output_edge("t")])
        assert "!= :p_0" in r["sql"]

    def test_gt(self):
        """Mutation target: > deve essere > non >=."""
        r = gen([self._src(">", "10", "int"), make_result()], [output_edge("t")])
        assert "> :p_0" in r["sql"]
        assert ">= :p_0" not in r["sql"]
        assert r["params"]["p_0"] == 10

    def test_gte(self):
        """Mutation target: >= deve essere >= non >."""
        r = gen([self._src(">=", "10", "int"), make_result()], [output_edge("t")])
        assert ">= :p_0" in r["sql"]
        assert r["params"]["p_0"] == 10

    def test_lt(self):
        r = gen([self._src("<", "100", "int"), make_result()], [output_edge("t")])
        assert "< :p_0" in r["sql"]
        assert "<= :p_0" not in r["sql"]

    def test_lte(self):
        r = gen([self._src("<=", "100", "int"), make_result()], [output_edge("t")])
        assert "<= :p_0" in r["sql"]

    def test_like_aggiunge_percent(self):
        """LIKE: valore wrappato con % automaticamente."""
        r = gen([self._src("LIKE", "Roma"), make_result()], [output_edge("t")])
        assert "LIKE :p_0" in r["sql"]
        assert r["params"]["p_0"] == "%Roma%"

    def test_is_null_nessun_parametro(self):
        r = gen([self._src("IS NULL", ""), make_result()], [output_edge("t")])
        assert "IS NULL" in r["sql"]
        assert not r["params"]

    def test_is_not_null(self):
        r = gen([self._src("IS NOT NULL", ""), make_result()], [output_edge("t")])
        assert "IS NOT NULL" in r["sql"]
        assert not r["params"]

    def test_in_stringa_csv(self):
        """IN con stringa CSV → spezzata in 3 valori separati."""
        r = gen([self._src("IN", "1,2,3"), make_result()], [output_edge("t")])
        assert "IN" in r["sql"]
        assert len(r["params"]) == 3
        assert set(r["params"].values()) == {1, 2, 3}

    def test_int_convertito_da_stringa(self):
        r = gen([self._src("=", "42", "int"), make_result()], [output_edge("t")])
        assert r["params"]["p_0"] == 42
        assert isinstance(r["params"]["p_0"], int)

    def test_real_convertito_da_stringa(self):
        r = gen([self._src("=", "3.14", "real"), make_result()], [output_edge("t")])
        assert abs(r["params"]["p_0"] - 3.14) < 1e-9
        assert isinstance(r["params"]["p_0"], float)

    def test_oracle_lob_wrappato_to_char(self):
        """Oracle + tipo str + alias → TO_CHAR(alias.col)."""
        src = make_source("t", "tab", [{"name": "col", "type": "str"}], alias="t")
        src.filters = [Filter(column="col", operator="=", value="testo")]
        r = gen([src, make_result()], [output_edge("t")], db_type="oracle")
        assert "TO_CHAR(t.col)" in r["sql"]

    def test_filtri_multipli_combinati_and(self):
        src = make_source("t", "tab", [
            {"name": "citta", "type": "str"},
            {"name": "anno",  "type": "int"},
        ])
        src.filters = [
            Filter(column="citta", operator="=",  value="Roma"),
            Filter(column="anno",  operator=">=", value="2020"),
        ]
        q = sql([src, make_result()], [output_edge("t")])
        assert "AND" in q
        assert "citta" in q
        assert "anno" in q


# =============================================================================
# JOIN
# =============================================================================

class TestJoin:

    def _two_table(self, join_type, db_type="postgres"):
        src_a = make_source("a", "clienti", [
            {"name": "id",   "type": "int"},
            {"name": "nome", "type": "str"},
        ])
        src_b = make_source("b", "ordini", [
            {"name": "cliente_id", "type": "int", "visible": False},
            {"name": "importo",    "type": "real"},
        ])
        res = make_result()
        j = make_edge("j", "a", "b", join_type=join_type,
                      source_col="id", target_col="cliente_id")
        return sql([src_a, src_b, res], [j, output_edge("b")], db_type=db_type)

    def test_inner_join(self):
        q = self._two_table("inner")
        assert "INNER JOIN" in q
        assert "clienti.id = ordini.cliente_id" in q

    def test_left_join(self):
        assert "LEFT JOIN" in self._two_table("left")

    def test_right_join(self):
        assert "RIGHT JOIN" in self._two_table("right")

    def test_full_outer_join(self):
        assert "FULL OUTER JOIN" in self._two_table("outer")

    def test_cross_join_senza_colonne(self):
        src_a = make_source("a", "tab_a", [{"name": "x", "type": "int"}])
        src_b = make_source("b", "tab_b", [{"name": "y", "type": "int"}])
        res = make_result()
        j = make_edge("j", "a", "b", join_type="inner", source_col="", target_col="")
        q = sql([src_a, src_b, res], [j, output_edge("b")])
        assert "JOIN" in q

    def test_join_mssql_schema_qualificato(self):
        q = self._two_table("inner", db_type="mssql")
        assert "dbo.clienti" in q and "dbo.ordini" in q

    def test_join_multi_condizione(self):
        src_a = make_source("a", "tab_a", [{"name": "id",     "type": "int"},
                                            {"name": "codice", "type": "str"}])
        src_b = make_source("b", "tab_b", [{"name": "tab_id",     "type": "int",  "visible": False},
                                            {"name": "tab_codice", "type": "str",  "visible": False},
                                            {"name": "valore",     "type": "real"}])
        j = make_edge("j", "a", "b", join_type="inner",
                      join_conditions=[
                          {"source_column": "id",     "target_column": "tab_id"},
                          {"source_column": "codice", "target_column": "tab_codice"},
                      ])
        q = sql([src_a, src_b, make_result()], [j, output_edge("b")])
        assert "AND" in q

    def test_spatial_join_st_intersects(self):
        src_a = make_source("a", "punti",    [{"name": "id",   "type": "int"},
                                               {"name": "geom", "type": "geometry"}])
        src_b = make_source("b", "poligoni", [{"name": "id",   "type": "int",  "visible": False},
                                               {"name": "geom", "type": "geometry", "visible": False},
                                               {"name": "nome", "type": "str"}])
        j = make_edge("j", "a", "b", join_type="inner",
                      is_spatial=True, spatial_predicate="ST_Intersects",
                      source_geom="geom", target_geom="geom")
        q = sql([src_a, src_b, make_result()], [j, output_edge("b")])
        assert "intersects" in q.lower()

    def test_spatial_join_dwithin(self):
        src_a = make_source("a", "punti",  [{"name": "geom", "type": "geometry"}])
        src_b = make_source("b", "target", [{"name": "geom", "type": "geometry", "visible": False},
                                             {"name": "id",   "type": "int"}])
        j = make_edge("j", "a", "b", join_type="inner",
                      is_spatial=True, spatial_predicate="ST_DWithin",
                      source_geom="geom", target_geom="geom", distance=500.0)
        q = sql([src_a, src_b, make_result()], [j, output_edge("b")])
        assert "dwithin" in q.lower()
        assert "500" in q


# =============================================================================
# GROUP BY
# =============================================================================

class TestGroupBy:

    def test_group_by_count_star(self):
        src = make_source("t", "vendite", [
            {"name": "comune",  "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["comune"],
            aggregations=[{"column": "*", "function": "COUNT", "alias": "totale"}],
        )
        q = sql([src, res], [output_edge("t")])
        assert "GROUP BY" in q
        assert "COUNT(*)" in q
        assert "AS totale" in q

    def test_group_by_sum(self):
        src = make_source("t", "vendite", [
            {"name": "comune",  "type": "str"},
            {"name": "importo", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["comune"],
            aggregations=[{"column": "importo", "function": "SUM", "alias": "tot"}],
        )
        q = sql([src, res], [output_edge("t")])
        assert "SUM(" in q
        assert "AS tot" in q

    def test_group_by_avg(self):
        src = make_source("t", "vendite", [
            {"name": "zona",   "type": "str"},
            {"name": "valore", "type": "real"},
        ])
        res = make_result(
            group_by_enabled=True,
            group_by_columns=["zona"],
            aggregations=[{"column": "valore", "function": "AVG", "alias": "media"}],
        )
        q = sql([src, res], [output_edge("t")])
        assert "AVG(" in q

    def test_distinct_ignorato_con_group_by(self):
        src = make_source("t", "tab", [{"name": "col", "type": "str"}])
        res = make_result(
            distinct=True,
            group_by_enabled=True,
            group_by_columns=["col"],
            aggregations=[{"column": "*", "function": "COUNT", "alias": "n"}],
        )
        q = sql([src, res], [output_edge("t")])
        assert "DISTINCT" not in q
        assert "GROUP BY" in q


# =============================================================================
# COLONNE CALCOLATE
# =============================================================================

class TestCalculatedColumns:

    def test_colonna_calcolata_sql_manuale(self):
        src = make_source("t", "tab", [
            {"name": "importo", "type": "real"},
        ], calculated_columns=[{
            "name": "iva",
            "is_manual": True,
            "sql": "t.importo * 0.22 AS iva",
        }])
        q = sql([src, make_result()], [output_edge("t")])
        assert "0.22" in q


# =============================================================================
# _is_valid_identifier — mutation targets
# =============================================================================

class TestIsValidIdentifier:

    @pytest.fixture
    def g(self):
        return qg()

    def test_nome_semplice_valido(self, g):
        assert g._is_valid_identifier("colonna") is True

    def test_nome_con_underscore_valido(self, g):
        assert g._is_valid_identifier("mia_colonna_1") is True

    def test_nome_esattamente_128_caratteri_valido(self, g):
        """Boundary: 128 caratteri → True."""
        assert g._is_valid_identifier("a" * 128) is True

    def test_nome_129_caratteri_non_valido(self, g):
        """Mutation target: > 128 → False. Se mutato in >= 128 fallisce qui."""
        assert g._is_valid_identifier("a" * 129) is False

    def test_stringa_vuota_non_valida(self, g):
        assert g._is_valid_identifier("") is False

    def test_none_non_valido(self, g):
        assert g._is_valid_identifier(None) is False  # type: ignore

    def test_drop_non_valido(self, g):
        assert g._is_valid_identifier("drop") is False
        assert g._is_valid_identifier("DROP") is False

    def test_delete_non_valido(self, g):
        assert g._is_valid_identifier("delete") is False

    def test_union_non_valido(self, g):
        assert g._is_valid_identifier("union") is False

    def test_insert_non_valido(self, g):
        assert g._is_valid_identifier("insert") is False

    def test_update_non_valido(self, g):
        assert g._is_valid_identifier("update") is False

    def test_trattino_non_valido(self, g):
        assert g._is_valid_identifier("col-name") is False

    def test_spazio_non_valido(self, g):
        assert g._is_valid_identifier("col name") is False

    def test_inizia_con_numero_non_valido(self, g):
        assert g._is_valid_identifier("1colonna") is False

    def test_exec_non_valido(self, g):
        assert g._is_valid_identifier("exec") is False

    def test_select_non_in_blacklist_finding(self, g):
        """
        FINDING B-BE-ESTRAI-01: 'select' NON è nella blacklist.
        Documenta il comportamento attuale — valutare se aggiungere
        select/from/where/having alla blacklist.
        """
        assert g._is_valid_identifier("select") is True  # comportamento attuale
